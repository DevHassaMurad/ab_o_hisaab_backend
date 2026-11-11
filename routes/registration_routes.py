"""Registration requires a capability-protected, server-validated verification process."""

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from config.database import get_db
from controller.authentication import Authentication
from controller.registration import Registration
from model.registration_schemas import RegistrationRequest, RegistrationResponse
from routes.auth_routes import auth_rate_limit

router = APIRouter(
    prefix="/auth", tags=["Registration"], dependencies=[Depends(auth_rate_limit)]
)


@router.post("/register", status_code=202, response_model=RegistrationResponse)
def register(
    data: RegistrationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    Authentication(db).queue_code(data.email, "email_confirmation", background_tasks)
    return Registration(db).create(data)
