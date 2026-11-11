from fastapi import Depends, HTTPException,status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from config.database import get_db
from jwt.exceptions import InvalidTokenError
from config.jwt_handler import JwtHandler
from model.user_model import User
class AuthMiddlware:
        def __init__(self):
            self.jwt_handler = JwtHandler()
            self.security = HTTPBearer()
            
        async def __call__(
            self,
            credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False)),
            db: Session = Depends(get_db)
        ):
            if credentials is None or credentials.scheme.lower() != "bearer":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                    headers={"WWW-Authenticate": "Bearer"}
                )
            try:
                payload = self.jwt_handler.verify_token(credentials.credentials)
            except InvalidTokenError as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=str(exc),
                    headers={"WWW-Authenticate": "Bearer"}
                ) from exc
            if payload is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token",
                    headers={"WWW-Authenticate": "Bearer"}
                )
            try:
                user_id = int(payload.get("sub"))
                
            except (KeyError,TypeError, ValueError):  
                raise HTTPException(
                    status_code=401,
                    detail="Invalid token"
                )
            user  = db.get(User, user_id)
            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found",
                    headers={"WWW-Authenticate": "Bearer"}
                )
            return user
        
auth_middleware = AuthMiddlware()
