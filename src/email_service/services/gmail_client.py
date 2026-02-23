import base64
import logging
import re
from datetime import datetime
from email.utils import parseaddr
from email.mime.text import MIMEText

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from email_service.config import settings

logger = logging.getLogger(__name__)


def get_gmail_service():
    creds = None

    if settings.gmail_token_path.exists():
        creds = Credentials.from_authorized_user_file(
            str(settings.gmail_token_path), settings.gmail_scopes
        )
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(settings.gmail_credentials_path), settings.gmail_scopes
            )
            creds = flow.run_local_server(port=9090, prompt="consent")

        settings.gmail_token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings.gmail_token_path, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def get_total_messages(service) -> int:
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("messagesTotal", 0)


def list_message_ids(service, max_results: int = 0) -> list[str]:
    ids = []
    page_token = None

    while True:
        batch_size = settings.import_batch_size
        if max_results > 0:
            remaining = max_results - len(ids)
            if remaining <= 0:
                break
            batch_size = min(batch_size, remaining)

        result = (
            service.users()
            .messages()
            .list(userId="me", maxResults=batch_size, pageToken=page_token)
            .execute()
        )

        messages = result.get("messages", [])
        ids.extend(msg["id"] for msg in messages)

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return ids


def fetch_email(service, gmail_id: str) -> dict:
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=gmail_id, format="full")
        .execute()
    )

    headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}

    from_raw = headers.get("from", "")
    from_name, from_address = parseaddr(from_raw)

    to_raw = headers.get("to", "")
    to_addresses = [addr.strip() for addr in to_raw.split(",") if addr.strip()]

    cc_raw = headers.get("cc", "")
    cc_addresses = [addr.strip() for addr in cc_raw.split(",") if addr.strip()]

    body_text = _extract_body(msg["payload"])
    attachments = _extract_attachments(msg["payload"])

    received_at = datetime.fromtimestamp(int(msg["internalDate"]) / 1000)

    return {
        "gmail_id": gmail_id,
        "thread_id": msg.get("threadId"),
        "from_address": from_address or from_raw,
        "from_name": from_name or None,
        "to_addresses": to_addresses,
        "cc_addresses": cc_addresses,
        "subject": headers.get("subject"),
        "body_text": body_text,
        "snippet": msg.get("snippet"),
        "received_at": received_at,
        "gmail_labels": msg.get("labelIds", []),
        "attachments": attachments,
        "rfc822_message_id": headers.get("message-id"),
    }


def send_email(
    service, to: str, subject: str, body: str, thread_id: str | None = None
) -> dict:
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    send_body: dict = {"raw": raw}
    if thread_id:
        send_body["threadId"] = thread_id

    sent = service.users().messages().send(userId="me", body=send_body).execute()
    logger.info(f"Email sent: id={sent['id']}, threadId={sent.get('threadId')}")
    return sent


def trash_email(service, gmail_id: str) -> None:
    service.users().messages().trash(userId="me", id=gmail_id).execute()
    logger.info(f"Email trashed in Gmail: {gmail_id}")


def block_sender(service, from_address: str) -> None:
    filter_body = {
        "criteria": {"from": from_address},
        "action": {"removeLabelIds": ["INBOX"], "addLabelIds": ["TRASH"]},
    }
    service.users().settings().filters().create(userId="me", body=filter_body).execute()
    logger.info(f"Bocked sender: {from_address}")


def get_unsubscribe_info(service, gmail_id: str) -> dict:
    msg = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=gmail_id,
            format="metadata",
            metadataHeaders=["List-Unsubscribe"],
        )
        .execute()
    )

    headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
    raw = headers.get("list-unsubscribe", "")

    if not raw:
        return {"found": False}

    result = {"found": True, "mailto": None, "url": None}
    mailto_match = re.search(r"<mailto:([^>]+)>", raw)
    if mailto_match:
        result["mailto"] = mailto_match.group(1)

    url_match = re.search(r"<(https?://[^>]+)>", raw)
    if url_match:
        result["url"] = url_match.group(1)

    return result


def _extract_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8")

    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result
    return ""


def _extract_attachments(payload: dict) -> list[dict]:
    """Recursively extract attachment metadata from Gmail payload"""
    attachments = []
    for part in payload.get("parts", []):
        filename = part.get("filename")
        if filename:
            attachments.append(
                {
                    "filename": filename,
                    "mime_type": part.get("mimeType", "application/octet-stream"),
                    "size_bytes": part.get("body", {}).get("size", 0),
                }
            )
        # recurse into nested multipart
        attachments.extend(_extract_attachments(part))
    return attachments
