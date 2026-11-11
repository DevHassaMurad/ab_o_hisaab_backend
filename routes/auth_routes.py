from fastapi import APIRouter, Depends, HTTPException
from middlewares.auth_middleware import auth_middleware
from controller.authentication import Authentication
from config.database import SessionLocal , get_db
from pydantic import BaseModel
from sqlalchemy.orm import Session
router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


class LoginRequest(BaseModel):
    email: str
    user_name: str
    password: str

        
@router.post("/authenticate")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    auth = Authentication(db)
    response, status_code = auth.authenticate(request.model_dump())
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=response["error"])
    return response


@router.get("/protected")
def protected_route(user=Depends(auth_middleware)):
    return {"message": f"Hello, {user.name}! You have accessed a protected route."}