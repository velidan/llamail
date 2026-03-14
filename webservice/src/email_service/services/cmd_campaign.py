"""
Campaign commands for Telegram handler:
create, load, personalize, preview, status, results, start, pause, resume
"""

import logging

from email_service.models.database import Campaign, CampaignRecipient, get_session
from email_service.services import campaign_engine

logger = logging.getLogger(__name__)


def handle_campaign(args: list[str]) -> str:
    if not args:
        return (
            "Usage: campaign (action) ...\n"
            "Actions: create, load, personalize, preview, status, results, start, pause, resume"
        )

    action = args[0].lower()
    other_args = args[1:]
    if action == "create":
        return campaign_create(other_args)
    if action == "load":
        return campaign_load(other_args)
    if action == "personalize":
        return campaign_personalize(other_args)
    if action == "preview":
        return campaign_preview(other_args)
    if action == "status":
        return campaign_engine.get_all_campaigns_status()
    if action == "start":
        return campaign_start(other_args)
    if action == "pause":
        return campaign_pause(other_args)
    if action == "resume":
        return campaign_resume(other_args)
    if action == "results":
        return campaign_results(other_args)

    return f"Unknown campaign action: {action}"


def campaign_create(args: list[str]) -> str:
    if len(args) < 2:
        return (
            "Usage: campaign create (name) (template_file) [subject_template]\n"
            "Example: campaign create winter2026 cover_letter.txt 'Application for {company_name}'"
        )
    name = args[0]
    template_file = args[1]
    subject_template = " ".join(args[2:]) if len(args) > 2 else None

    return campaign_engine.create_campaign(name, template_file, subject_template)


def campaign_load(args: list[str]) -> str:
    if len(args) < 2:
        return (
            "Usage: campaign load (name) (csv_file)\n"
            "Example: campaign load winter 2026 companies.csv"
        )

    name = args[0]
    csv_path = args[1]

    return campaign_engine.load_recipients(name, csv_path)


def campaign_personalize(args: list[str]) -> str:
    if not args:
        return "Usage: campaign personalize (name)\nExample: campaign personalize winter2026"

    return campaign_engine.personalize_campaign(args[0])


def campaign_preview(args: list[str]) -> str:
    if not args:
        return "Usage: campaign preview (name) [count]\nExample: campaign preview winter2026 5"

    name = args[0]
    count = 3
    if len(args) > 1:
        try:
            count = int(args[1])
        except ValueError:
            return f"Invalid count: {args[1]}"

    return campaign_engine.preview_campaign(name, count)


def campaign_results(args: list[str]) -> str:
    if not args:
        return "Usage: campaign results (name)\nExample: campaign results winter2026"
    return campaign_engine.get_campaign_results(args[0])


def campaign_start(args: list[str]) -> str:
    if not args:
        return "Usage: campaign start (name)"

    name = args[0]
    session = get_session()
    try:
        campaign = session.query(Campaign).filter_by(name=name).first()
        if not campaign:
            return f"Campaign '{name}' not found"

        if campaign.status == "running":
            return f"Campaign '{name}' is already running"

        ready = (
            session.query(CampaignRecipient)
            .filter_by(campaign_id=campaign.id, status="personalized")
            .count()
        )
        if ready == 0:
            return f"No personalized recipients in '{name}'. Run: campaign personalize {name}"

        campaign.status = "running"
        session.commit()
        return f"Campaign '{name}' started - {ready} emails queued to send"
    finally:
        session.close()


def campaign_pause(args: list[str]) -> str:
    if not args:
        return "Usage: campaign pause (name)"

    name = args[0]
    session = get_session()
    try:
        campaign = session.query(Campaign).filter_by(name=name).first()
        if not campaign:
            return f"Campaign '{name}' not found"

        if campaign.status != "running":
            return f"Campaign '{name}' is not running (status: {campaign.status})"

        campaign.status = "paused"
        session.commit()
        return f"Campaign '{name}' paused"
    finally:
        session.close()


def campaign_resume(args: list[str]) -> str:
    if not args:
        return "Usage: campaign resume (name)"

    name = args[0]
    session = get_session()
    try:
        campaign = session.query(Campaign).filter_by(name=name).first()
        if not campaign:
            return f"Campaign '{name}' not found"

        if campaign.status not in ("paused", "completed"):
            return f"Campaign '{name}' cannot be resumed (status: {campaign.status})"

        remaining = (
            session.query(CampaignRecipient)
            .filter_by(campaign_id=campaign.id, status="personalized")
            .count()
        )
        if remaining == 0:
            return f"No unsent recipients in '{name}'"

        campaign.status = "running"
        session.commit()
        return f"Campaign '{name}' resumed - {remaining} emails remaining"
    finally:
        session.close()
