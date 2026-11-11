"""Wire schemas use snake_case; the portal maps its form keys explicitly."""

import re
from decimal import Decimal
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    model_validator,
)

Text100 = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class UserType(StrEnum):
    donor = "donor"
    ngo = "ngo"
    householder = "householder"
    shopkeeper = "shopkeeper"


class DocumentSlot(StrEnum):
    cnicFront = "cnicFront"
    cnicBack = "cnicBack"
    passportCopy = "passportCopy"
    addressPhoto = "addressPhoto"
    addressProof = "addressProof"
    ngoCertificate = "ngoCertificate"
    authorization = "authorization"
    businessProof = "businessProof"
    shopPhoto = "shopPhoto"


DOCUMENT_TYPES = {
    "cnicFront": "cnic",
    "cnicBack": "cnic",
    "passportCopy": "passport",
    "addressPhoto": "home_address",
    "addressProof": "home_address",
    "ngoCertificate": "ngo_certificate",
    "authorization": "authorization",
    "businessProof": "business_proof",
    "shopPhoto": "shop_photo",
}


def document_slots(user_type, identity_type):
    slots = ["cnicFront", "cnicBack"] if identity_type == "cnic" else ["passportCopy"]
    return (
        slots
        + ["addressPhoto", "addressProof"]
        + {
            "ngo": ["ngoCertificate", "authorization"],
            "shopkeeper": ["businessProof", "shopPhoto"],
        }.get(user_type, [])
    )


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verification_id: str = Field(min_length=36, max_length=36)
    verification_token: str = Field(min_length=40, max_length=100)
    ngo_verification_id: str | None = Field(default=None, min_length=36, max_length=36)
    ngo_verification_token: str | None = Field(default=None, min_length=40, max_length=100)
    user_type: UserType
    full_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
    ]
    email: EmailStr = Field(max_length=100)
    password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(min_length=12, max_length=128)
    phone: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[\d\s()-]{7,20}$")
    ]
    identity_type: Literal["cnic", "passport"]
    identity_number: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=5, max_length=20)
    ]
    country: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z]{2}$")
    ]
    state: Text100
    city: Text100
    postal_code: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)
    ]
    address1: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    address2: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] | None
    ) = None
    address_confirmed: bool = False
    consent: Literal[True]
    donor_type: Literal["national", "international"] | None = None
    organization_name: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
        ]
        | None
    ) = None
    registration_number: Text100 | None = None
    designation: Text100 | None = None
    household_size: int | None = Field(default=None, ge=1, le=10000)
    dependents: int | None = Field(default=None, ge=0, le=9999)
    # Householder composition and monthly income range (PKR); size and dependents are derived from these.
    adults: int | None = Field(default=None, ge=1, le=100)
    children: int | None = Field(default=None, ge=0, le=100)
    males: int | None = Field(default=None, ge=0, le=200)
    females: int | None = Field(default=None, ge=0, le=200)
    income_min: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999.99"), max_digits=14, decimal_places=2)
    income_max: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999.99"), max_digits=14, decimal_places=2)
    # Shopkeeper monthly revenue range (PKR).
    revenue_min: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999.99"), max_digits=14, decimal_places=2)
    revenue_max: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999.99"), max_digits=14, decimal_places=2)
    shop_name: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
        ]
        | None
    ) = None
    business_number: Text100 | None = None
    business_category: Text100 | None = None

    @model_validator(mode="after")
    def validate_details(self):
        if self.user_type == "ngo" and not (self.ngo_verification_id and self.ngo_verification_token):
            raise ValueError("NGOs require identity and NGO certificate verification.")
        if self.user_type != "ngo" and (self.ngo_verification_id or self.ngo_verification_token):
            raise ValueError("NGO certificate verification is only for NGOs.")
        if self.password != self.confirm_password:
            raise ValueError("Passwords must match.")
        if self.identity_type == "cnic":
            self.identity_number = self.identity_number.replace("-", "")
            if not re.fullmatch(r"[0-9]{13}", self.identity_number):
                raise ValueError("Enter a 13-digit CNIC, with or without dashes.")
        elif not re.fullmatch(r"[a-zA-Z0-9 -]{5,20}", self.identity_number):
            raise ValueError("Enter a valid passport number.")
        if self.user_type == "householder" and self.adults is not None and self.children is not None:
            self.household_size = self.adults + self.children
            self.dependents = self.children
            if (self.males or 0) + (self.females or 0) != self.household_size:
                raise ValueError("Males plus females must equal adults plus children.")
        for low, high, label in (("income_min", "income_max", "income"), ("revenue_min", "revenue_max", "revenue")):
            lo, hi = getattr(self, low), getattr(self, high)
            if lo is not None and hi is not None and hi < lo:
                raise ValueError(f"The maximum monthly {label} cannot be below the minimum.")
        fields = ROLE_FIELDS[self.user_type]
        missing = [key for key in fields if getattr(self, key) is None]
        if missing:
            raise ValueError("Required for this user type: " + ", ".join(missing))
        if self.user_type == "householder" and self.dependents >= self.household_size:
            raise ValueError("Dependents must be less than household size.")
        for role, keys in ROLE_FIELDS.items():
            if role != self.user_type and any(
                getattr(self, key) is not None for key in keys
            ):
                raise ValueError("Do not include details for another user type.")
        self.email = str(self.email).lower()
        return self


ROLE_FIELDS = {
    "donor": ["donor_type"],
    "ngo": ["organization_name", "registration_number", "designation"],
    "householder": ["household_size", "dependents", "adults", "children", "males", "females", "income_min",
                    "income_max"],
    "shopkeeper": ["shop_name", "business_number", "business_category", "revenue_min", "revenue_max"],
}


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    document_url: str
    status: Literal["Pending", "Approved", "Rejected"]
    user_type: UserType
    document_type: str
    document_slot: DocumentSlot
    approval_type: Literal["ai", "manual"] | None
    verification_attempts: int
    review_requested_at: datetime | None
    verification_result: dict | None
    verified_by: int | None
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RegistrationResponse(BaseModel):
    message: str
    user_id: int
    user_type: UserType
    email_verification_required: bool = True
    registration_status: str
    documents: list[DocumentResponse]


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_type: Literal["manual", "ai"] = "manual"
    status: Literal["Approved", "Rejected"] | None = None
    reason: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None
    ) = None

    @model_validator(mode="after")
    def validate_method(self):
        if self.approval_type == "manual" and self.status is None:
            raise ValueError("Manual review requires Approved or Rejected status.")
        if self.approval_type == "ai" and (
            self.status is not None or self.reason is not None
        ):
            raise ValueError("AI results must come from the configured service.")
        return self


class AIVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["Approved", "Rejected"]
    reason: str = Field(max_length=1000)
    provider: str = Field(min_length=1, max_length=100)
