from sqlalchemy import Boolean, ForeignKey, Integer, String, true
from sqlalchemy.orm import Mapped, mapped_column
from config.database import Base
from model.role_model import Role

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", name="fk_users_role_id"),
        nullable=False, default=0, server_default="0",
    )
    # District FK will be added with the geography migration.
    district_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(),
    )
