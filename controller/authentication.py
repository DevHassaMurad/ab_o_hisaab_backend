from sqlalchemy import select, or_
from pwdlib import PasswordHash
from model.user_model import User
from model.role_model import Role
from config.jwt_handler import JwtHandler


class Authentication:

    def __init__(self, db_session):
        self.db_session = db_session
        self.password = PasswordHash.recommended()
        self.jwt_handler = JwtHandler()

    def register_user(self, data):
        existing_user = self.db_session.execute(
            select(User).where(
                or_(User.email == data["email"], User.name == data["user_name"])
            )
        ).scalar_one_or_none()
        if existing_user:
            return {"error": "User with this email or username already exists."}, 400
        password = self.password.hash(data["password"])

        user = User(name=data["user_name"], email=data["email"], password=password)
        self.db_session.add(user)
        self.db_session.commit()
        return {"message": "User registered successfully."}, 201

    def authenticate(self, data):
        user = self.db_session.execute(
            select(User).where(
                or_(User.email == data["email"], User.name == data["user_name"])
            )
        ).scalar_one_or_none()

        if user and self.password.verify(data["password"], user.password):
            if not user.is_active:
                return {"error": "This account is disabled. Contact your PDMA administrator."}, 403
            role = self.db_session.get(Role, user.role_id)
            if role is None or role.name == "UNASSIGNED":
                return {"error": "This account has no role yet. Contact your PDMA administrator."}, 403
            access_token = self.jwt_handler.create_token(
                user_id=user.id, role=role.name, district_id=user.district_id,
                name=user.name, email=user.email,
            )

            return {
                "message": "Authentication successful.",
                "access_token": access_token,
                "token_type": "bearer",
            }, 200
        else:
            return {"error": "Invalid email/username or password."}, 401
