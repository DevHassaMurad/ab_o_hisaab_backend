from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship
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
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    registration_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    donor_details = relationship('DonorDetails', back_populates='user', uselist=False, cascade='all, delete-orphan', passive_deletes=True)
    ngo_details = relationship('NgoDetails', back_populates='user', uselist=False, cascade='all, delete-orphan', passive_deletes=True)
    householder_details = relationship('HouseholderDetails', back_populates='user', uselist=False, cascade='all, delete-orphan', passive_deletes=True)
    shopkeeper_details = relationship('ShopkeeperDetails', back_populates='user', uselist=False, cascade='all, delete-orphan', passive_deletes=True)
    documents = relationship('Document', foreign_keys='Document.user_id', back_populates='user', cascade='all, delete-orphan', passive_deletes=True)

# Register authentication challenge metadata with the existing declarative base.
from model.auth_challenge_model import AuthChallenge  # noqa: E402,F401
from model.registration_model import DonorDetails, NgoDetails, HouseholderDetails, ShopkeeperDetails  # noqa: E402,F401
from model.document_model import Document  # noqa: E402,F401
from model.verification_model import VerificationProcess  # noqa: E402,F401
