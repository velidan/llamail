from fastapi import APIRouter

from email_service.models.schemas import ProcessEmailRequest, ProcessEmailResponse
from email_service.services.email_processor import process_email

router = APIRouter()


@router.post("/process-email", response_model=ProcessEmailResponse)
def handle_process_email(request: ProcessEmailRequest):
    return process_email(request)
