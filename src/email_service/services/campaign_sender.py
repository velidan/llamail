import logging
import threading
import time
from datetime import datetime, timedelta

from email_service.config import settings
from email_service.models.database import Campaign, CampaignRecipient, get_session
from email_service.services import gmail_client

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_thread: threading.Thread | None = None


def start_sender():
    global _thread
    if _thread and _thread.is_alive():
        logger.warning("Campaign sender already running")
        return

    _stop_event.clear()
    _thread = threading.Thread(target=_sender_loop, daemon=True)
    _thread.start()
    logger.info(
        "campaign sender started (interval: %ds)", settings.campaign_check_interval
    )


def stop_sender():
    _stop_event.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)
    logger.info("Campaign sender stopped")


def _sender_loop():
    while not _stop_event.is_set():
        try:
            _process_running_campaigns()
            _check_ghosted()
        except Exception as e:
            logger.error(f"Campaign sender error: {e}")

        _stop_event.wait(timeout=settings.campaign_check_interval)


def _process_running_campaigns():
    session = get_session()
    try:
        campaigns = session.query(Campaign).filter(Campaign.status == "running").all()
        # save data before close
        campaign_data = [
            {
                "id": c.id,
                "name": c.name,
                "send_rate": c.send_rate,
                "attachment_file": c.attachment_file,
            }
            for c in campaigns
        ]
    finally:
        session.close()

    if not campaign_data:
        return

    service = gmail_client.get_gmail_service()

    for cd in campaign_data:
        if _stop_event.is_set():
            break
        _send_next(service, cd)


def _send_next(service, campaign_data: dict) -> None:
    campaign_id = campaign_data["id"]
    send_rate = campaign_data["send_rate"]
    # seconds between emails
    delay = 3600 / send_rate

    attachment_path = None
    if campaign_data["attachment_file"]:
        attachment_path = str(settings.campaigns_dir / campaign_data["attachment_file"])

    session = get_session()
    try:
        recipient = (
            session.query(CampaignRecipient)
            .filter_by(campaign_id=campaign_id, status="personalized")
            .first()
        )
        if not recipient:
            # all done - mark campaign completed
            campaign = session.query(Campaign).get(campaign_id)
            if campaign:
                campaign.status = "completed"
                session.commit()
                logger.info(f"Campaign '{campaign_data['name']}' completed")
            return

        # save before close
        rid = recipient.id
        to_address = recipient.to_address
        subject = recipient.personalized_subject
        body = recipient.personalized_body
    finally:
        session.close()

    try:
        sent = gmail_client.send_email(
            service,
            to=to_address,
            subject=subject,
            body=body,
            attachment_path=attachment_path,
        )

        session = get_session()
        try:
            r = session.query(CampaignRecipient).get(rid)
            r.status = "sent"
            r.sent_at = datetime.now()
            r.gmail_message_id = sent.get("id")
            r.gmail_thread_id = sent.get("threadId")

            campaign = session.query(Campaign).get(campaign_id)
            campaign.sent_count += 1
            session.commit()
            logger.info(f"Campaign email sent to {to_address}")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Campaign send failed for {to_address}: {e}")

    # throttle
    if not _stop_event.is_set():
        _stop_event.wait(timeout=delay)


def _check_ghosted():
    cutoff = datetime.now() - timedelta(days=14)
    session = get_session()
    try:
        ghosted = (
            session.query(CampaignRecipient)
            .filter(
                CampaignRecipient.status == "sent", CampaignRecipient.sent_at < cutoff
            )
            .all()
        )

        if not ghosted:
            return

        campaign_ids = set()
        for r in ghosted:
            r.status = "classified"
            r.reply_classification = "ghosted"
            campaign_ids.add(r.campaign_id)

        session.commit()
        logger.info(f"Marked {len(ghosted)} recipients as ghosted")
    finally:
        session.close()
