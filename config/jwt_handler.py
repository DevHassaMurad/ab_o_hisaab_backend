import os
import jwt 
from datetime import datetime,timedelta,timezone
from dotenv import load_dotenv
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
load_dotenv()

class JwtHandler:
    def __init__(self):
        self.secret_key = os.environ.get("JWT_SECRET_KEY")
        self.algorithm = os.environ.get("JWT_ALGORITHM") or "HS256"
        self.expire_minutes = int(os.environ.get("JWT_EXPIRE_MINUTES", 30))
        
    def create_token(self, user_id, token_version=0, role=None, district_id=None, name=None, email=None):
        """The portal reads role and district_id from the token to pick the user's pages.
        Every API must still check the role server-side; the token claims are not a permission by themselves."""
        expire = datetime.now(timezone.utc) + timedelta(minutes=self.expire_minutes)
        payload = {
            "sub" : str(user_id),
            "exp" : expire,
            "iat" : datetime.now(timezone.utc),
            "type" : "access",
            "role" : role,
            "district_id" : district_id,
            "name" : name,
            "email" : email,
            "ver": token_version,
        }
        token = jwt.encode(
            payload,
            self.secret_key,
            algorithm=self.algorithm
        )
        return token
    
    def verify_token(self,token):
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={"require": ["sub", "exp", "iat"]}
                )
            if payload.get("type") != "access":
                raise InvalidTokenError("Invalid token type")
            return payload
        except ExpiredSignatureError:
            raise ExpiredSignatureError("Token has expired")
        except InvalidTokenError:
            raise InvalidTokenError("Invalid token")
