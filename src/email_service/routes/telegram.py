from fastapi import APIRouter

from email_service.models.schemas import TelegramCommandRequest, TelegramCommandResponse
from email_service.services.telegram_handler import handle_command

router = APIRouter()


@router.post("/telegram/command", response_model=TelegramCommandResponse)
def telegram_command(request: TelegramCommandRequest):
    reply = handle_command(request.text)
    return TelegramCommandResponse(reply=reply)
