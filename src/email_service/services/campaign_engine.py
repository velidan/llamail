import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from email_service.config import settings
from email_service.models.database import Campaign, CampaignRecipient, get_session
from email_service.services import llm
from email_service.services import telegram_notifier
from email_service.services.utils import parse_json

logger = logging.getLogger(__name__)

_template_dir = Path(__file__).parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_template_dir)))
_personalize = _env.get_template("personalize.j2")
_classify_reply = _env.get_template("classify_reply.j2")

# --- Campaign CRUD ---


def create_campaign(
    name: str,
    template_file: str,
    subject_template: str | None = None,
    attachment_file: str | None = None,
) -> str:
    # check if tempalte exist
    template_path = settings.campaigns_dir / template_file
    if not template_path.exists():
        return f"Template not found: {template_path}"

    if attachment_file:
        attach_path = settings.campaigns_dir / attachment_file
        if not attach_path.exists():
            return f"Attachment not found: {attach_path}"

    session = get_session()
    try:
        existing = session.query(Campaign).filter_by(name=name).first()
        if existing:
            return f"Campaign '{name}' already exists"

        campaign = Campaign(
            name=name,
            template_file=template_file,
            subject_template=subject_template,
            attachment_file=attachment_file,
        )
        session.add(campaign)
        session.commit()
        return f"Campaign '{name}' created"
    finally:
        session.close()


def load_recipients(name: str, csv_path: str) -> str:
    csv_file = Path(csv_path)
    if not csv_file.exists():
        # try inside campaigns_dir
        csv_file = settings.campaigns_dir / csv_path
        if not csv_file.exists():
            return f"CSV not found: {csv_path}"

    session = get_session()
    try:
        campaign = session.query(Campaign).filter_by(name=name).first()
        if not campaign:
            return f"Campaign '{name}' not found"

        with open(csv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                recipient = CampaignRecipient(
                    campaign_id=campaign.id,
                    company_name=row.get("company_name", ""),
                    to_address=row.get("to_address", ""),
                    contact_name=row.get("contact_name") or None,
                    company_info=row.get("company_info") or None,
                )
                session.add(recipient)
                count += 1
            campaign.total_recipients = count
            session.commit()
            return f"Loaded {count} recipients into '{name}'"
    finally:
        session.close()


def personalize_campaign(name: str) -> str:
    session = get_session()
    try:
        campaign = session.query(Campaign).filter_by(name=name).first()
        if not campaign:
            return f"Campaign '{name}' not found"

        template_path = settings.campaigns_dir / campaign.template_file
        template_body = template_path.read_text(encoding="utf-8")
        subject_template = campaign.subject_template
        campaign_id = campaign.id
    finally:
        session.close()

    # fetch pending recipients
    session = get_session()
    try:
        recipients = (
            session.query(CampaignRecipient)
            .filter_by(campaign_id=campaign_id, status="pending")
            .all()
        )
        # save attrs before closing
        recipient_data = [
            {
                "id": r.id,
                "company_name": r.company_name,
                "to_address": r.to_address,
                "contact_name": r.contact_name,
                "company_info": r.company_info,
            }
            for r in recipients
        ]
    finally:
        session.close()

    if not recipient_data:
        return f"No pending recipients in '{name}'"

    done = 0
    for rd in recipient_data:
        prompt = _personalize.render(
            template_body=template_body,
            company_name=rd["company_name"],
            to_address=rd["to_address"],
            contact_name=rd["contact_name"],
            company_info=rd["company_info"],
            subject_template=subject_template,
        )

        raw = llm.generate(prompt)
        parsed = parse_json(raw)

        session = get_session()
        try:
            r = session.query(CampaignRecipient).get(rd["id"])
            r.personalized_subject = parsed.get("personalized_subject", "")
            r.personalized_body = parsed.get("personalized_body", "")
            r.status = "personalized"
            session.commit()
            done += 1
        finally:
            session.close()

    return f"Personalized {done}/{len(recipient_data)} recipients in '{name}'"


def preview_campaign(name: str, count: int = 3) -> str:
    session = get_session()
    try:
        campaign = session.query(Campaign).filter_by(name=name).first()
        if not campaign:
            return f"Campaign '{name}' not found"

        recipients = (
            session.query(CampaignRecipient)
            .filter_by(campaign_id=campaign.id, status="personalized")
            .limit(count)
            .all()
        )

        if not recipients:
            return f"No personalized recipients yet. Run: campaign personalize {name}"

        lines = [f"Preview for '{name}' ({len(recipients)} shown):\n"]
        for i, r in enumerate(recipients, 1):
            lines.append(f"--- #{i}: {r.company_name} ({r.to_address}) ---")
            lines.append(f"Subject: {r.personalized_subject}")
            lines.append(f"{r.personalized_body}\n")

        return "\n".join(lines)
    finally:
        session.close()


def get_all_campaigns_status() -> str:
    session = get_session()
    try:
        campaigns = session.query(Campaign).all()
        if not campaigns:
            return "No campaigns found"

        lines = ["Campaigns:\n"]
        for c in campaigns:
            lines.append(
                f"  {c.name} [{c.status}] — "
                f"{c.sent_count}/{c.total_recipients} sent, "
                f"{c.reply_count} replies, "
                f"{c.interview_count} interviews"
            )
        return "\n".join(lines)
    finally:
        session.close()


def get_campaign_results(name: str) -> str:
    session = get_session()
    try:
        campaign = session.query(Campaign).filter_by(name=name).first()
        if not campaign:
            return f"Campaign '{name}' not found"

        lines = [
            f"Results for '{name}' [{campaign.status}]:\n",
            f"  Total: {campaign.total_recipients}",
            f"  Sent: {campaign.sent_count}",
            f"  Replies: {campaign.reply_count}",
            f"  Interviews: {campaign.interview_count}",
            f"  Rejections: {campaign.rejection_count}",
        ]

        # show replied/classified recipients
        replied = (
            session.query(CampaignRecipient)
            .filter_by(campaign_id=campaign.id)
            .filter(CampaignRecipient.reply_classification.isnot(None))
            .all()
        )

        if replied:
            lines.append("\nReplies:")
            for r in replied:
                lines.append(f"  {r.company_name} — {r.reply_classification}")

        return "\n".join(lines)
    finally:
        session.close()


def check_campaign_reply(thread_id: str, reply_body: str) -> dict | None:
    """Match an incoming email by thread_id to a campaign recipient.
    If found, classify the reply with LLM and update counters.
    Returns dict with result info, or None if not a campaign reply.
    """

    session = get_session()
    try:
        recipient = (
            session.query(CampaignRecipient)
            .filter_by(gmail_thread_id=thread_id)
            .first()
        )
        if not recipient:
            return None

        # save before close
        rid = recipient.id
        campaign_id = recipient.campaign_id
        company_name = recipient.company_name
        contact_name = recipient.contact_name
        to_address = recipient.to_address
    finally:
        session.close()

    # classify with LLM
    prompt = _classify_reply.render(
        company_name=company_name,
        contact_name=contact_name,
        to_address=to_address,
        reply_body=reply_body,
    )
    raw = llm.generate(prompt)
    parsed = parse_json(raw)

    classification = parsed.get("classification", "follow_up")
    summary = parsed.get("summary", "")

    # update recipient
    session = get_session()
    try:
        r = session.query(CampaignRecipient).get(rid)
        r.status = "classified"
        r.reply_classification = classification
        r.replied_at = datetime.now()

        # update campaign counters
        campaign = session.query(Campaign).get(campaign_id)
        campaign.reply_count += 1
        if classification == "interview":
            campaign.interview_count += 1
        elif classification == "rejection":
            campaign.rejection_count += 1

        session.commit()

        # push notification for action replies
        if classification in ("interview", "follow_up"):
            telegram_notifier.notify(
                f"Campaign reply from {company_name}!\n"
                f"Classification: {classification}\n"
                f"Summary: {summary}"
            )
    finally:
        session.close()

    return {
        "company_name": company_name,
        "classification": classification,
        "summary": summary,
    }
