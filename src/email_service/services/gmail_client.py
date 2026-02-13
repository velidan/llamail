import base64
import logging
from datetime import datetime
from email.utils import parseaddr

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
            creds = flow.run_local_server(port=9090)

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
        "rfc822_message_id": headers.get("message-id"),
    }


def _extract_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8")

    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result
    return ""
