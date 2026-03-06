from fastapi import APIRouter

from email_service.models.schemas import TelegramCommandRequest, TelegramCommandResponse
from email_service.services.telegram_handler import handle_command

router = APIRouter()

TELEGRAM_MAX_LENGTH = 4000


@router.post("/telegram/command", response_model=TelegramCommandResponse)
def telegram_command(request: TelegramCommandRequest):
    reply = handle_command(request.text, request.chat_id)
    if len(reply) > TELEGRAM_MAX_LENGTH:
        reply = reply[:TELEGRAM_MAX_LENGTH] + "\n...{truncated}"
    return TelegramCommandResponse(reply=reply)
