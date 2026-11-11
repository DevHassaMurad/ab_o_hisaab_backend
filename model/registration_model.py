"""Registration profiles share the existing authentication declarative base."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.database import Base


class ProfileColumns:
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    identity_type: Mapped[str] = mapped_column(String(8), nullable=False)
    identity_number: Mapped[str] = mapped_column(String(20), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    address1: Mapped[str] = mapped_column(String(255), nullable=False)
    address2: Mapped[str | None] = mapped_column(String(255))
    address_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    consent_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DonorDetails(ProfileColumns, Base):
    __tablename__ = "donor_details"
    __table_args__ = (
        CheckConstraint(
            "identity_type IN ('cnic', 'passport')", name="ck_donor_identity_type"
        ),
        CheckConstraint(
            "donor_type IN ('national', 'international')", name="ck_donor_type"
        ),
    )
    donor_type: Mapped[str] = mapped_column(String(13), nullable=False)
    user = relationship("User", back_populates="donor_details")


class NgoDetails(ProfileColumns, Base):
    __tablename__ = "ngo_details"
    __table_args__ = (
        CheckConstraint(
            "identity_type IN ('cnic', 'passport')", name="ck_ngo_identity_type"
        ),
    )
    organization_name: Mapped[str] = mapped_column(String(200), nullable=False)
    registration_number: Mapped[str] = mapped_column(String(100), nullable=False)
    designation: Mapped[str] = mapped_column(String(100), nullable=False)
    user = relationship("User", back_populates="ngo_details")


class HouseholderDetails(ProfileColumns, Base):
    __tablename__ = "householder_details"
    __table_args__ = (
        CheckConstraint(
            "identity_type IN ('cnic', 'passport')", name="ck_householder_identity_type"
        ),
        CheckConstraint(
            "household_size >= 1 AND dependents >= 0 AND dependents < household_size",
            name="ck_household_counts",
        ),
        CheckConstraint("income_min IS NULL OR income_max IS NULL OR income_max >= income_min",
                        name="ck_household_income_range"),
    )
    household_size: Mapped[int] = mapped_column(Integer, nullable=False)
    dependents: Mapped[int] = mapped_column(Integer, nullable=False)
    # Household composition and monthly income range (migration 0014). Older rows may be NULL.
    adults: Mapped[int | None] = mapped_column(Integer, nullable=True)
    children: Mapped[int | None] = mapped_column(Integer, nullable=True)
    males: Mapped[int | None] = mapped_column(Integer, nullable=True)
    females: Mapped[int | None] = mapped_column(Integer, nullable=True)
    income_min = mapped_column(Numeric(14, 2), nullable=True)
    income_max = mapped_column(Numeric(14, 2), nullable=True)
    user = relationship("User", back_populates="householder_details")


class ShopkeeperDetails(ProfileColumns, Base):
    __tablename__ = "shopkeeper_details"
    __table_args__ = (
        CheckConstraint(
            "identity_type IN ('cnic', 'passport')", name="ck_shopkeeper_identity_type"
        ),
        CheckConstraint("revenue_min IS NULL OR revenue_max IS NULL OR revenue_max >= revenue_min",
                        name="ck_shop_revenue_range"),
    )
    # Monthly revenue range (migration 0014). Older rows may be NULL.
    revenue_min = mapped_column(Numeric(14, 2), nullable=True)
    revenue_max = mapped_column(Numeric(14, 2), nullable=True)
    shop_name: Mapped[str] = mapped_column(String(200), nullable=False)
    business_number: Mapped[str] = mapped_column(String(100), nullable=False)
    business_category: Mapped[str] = mapped_column(String(100), nullable=False)
    user = relationship("User", back_populates="shopkeeper_details")


PROFILE_MODELS = {
    "donor": DonorDetails,
    "ngo": NgoDetails,
    "householder": HouseholderDetails,
    "shopkeeper": ShopkeeperDetails,
}
