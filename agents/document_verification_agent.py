"""Real local CV + Tesseract analysis with an optional trusted authenticity service.

No trained authenticity classifier is claimed. Local content checks never approve
official documents; approval additionally requires explicit policy and trusted evidence.
"""

import base64
import hashlib
import json
import os

from cryptography.fernet import Fernet

from agents.document_classifier import classify
from agents.document_preprocessor import decode_pages
from agents.document_validator import validate_content

STAGES = ["scanning", "enhancing", "extracting", "validating", "completing"]


def cipher():
    configured = os.environ.get("DOCUMENT_DATA_KEY")
    if configured:
        return Fernet(configured.encode())
    secret = os.environ.get("AUTH_CODE_SECRET") or os.environ.get("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError(
            "Configure DOCUMENT_DATA_KEY or the existing authentication secret."
        )
    return Fernet(
        base64.urlsafe_b64encode(
            hashlib.sha256(("document-extraction-v1:" + secret).encode()).digest()
        )
    )


def seal_details(details):
    return cipher().encrypt(json.dumps(details).encode()).decode() if details else None


def identity_matches(encrypted_details, identity_number):
    if not encrypted_details:
        return True
    details = json.loads(cipher().decrypt(encrypted_details.encode()))
    found = details.get("identity_number")
    normalize = lambda value: "".join(char for char in value.upper() if char.isalnum())
    return not found or normalize(found) == normalize(identity_number)


class DocumentVerificationAgent:
    def analyze(self, path, content_type, document_type, progress):
        progress("scanning", False)
        pages = decode_pages(path, content_type)
        progress("scanning", True)
        progress("enhancing", False)
        progress("enhancing", True)
        progress("extracting", False)
        from agents.grok_extractor import extract
        output = extract(pages, document_type)
        text = output.text
        # Canonical labels let deterministic rules validate visible structured fields.
        labels = {"identity_number": "Passport number" if document_type == "passport" else "CNIC", "surname": "Surname", "given_names": "Given names", "nationality": "Nationality", "date_of_birth": "Date of birth", "organization_name": "Organization name", "registration_number": "Registration number"}
        for key, label in labels.items():
            if getattr(output, key):
                text += "\n" + label + ": " + getattr(output, key)
        word_count = len(text.split())
        metrics = []
        issues = []
        progress("extracting", True)
        progress("validating", False)
        classification = classify(text)
        checks, extracted, validation_issues = validate_content(
            document_type, text, classification
        )
        extracted.update({key: value for key, value in output.model_dump().items() if key not in ("text", "document_type", "extraction_source", "extraction_warning") and value is not None})
        issues.extend(validation_issues)
        if word_count < 12:
            issues.append("Not enough document text is readable for automated checks.")
        checks["layout_has_multiple_lines"] = len(text.splitlines()) >= 3
        if not checks["layout_has_multiple_lines"]:
            issues.append("The expected document text layout could not be established.")
        authenticity = (
            self.check_authenticity(path, document_type)
            if not issues
            else {"verified": False, "provider": None}
        )
        passed = not issues
        approved = (
            passed
            and not (document_type == "passport" and os.environ.get("DOCUMENT_PASSPORT_MODE") == "extraction")
            and authenticity["verified"]
            and os.environ.get("DOCUMENT_AUTO_APPROVE", "false").lower() == "true"
        )
        progress("validating", True)
        progress("completing", False)
        result = {
            "automated_checks_passed": passed,
            "authenticity_verified": bool(authenticity["verified"]),
            "manual_review_required": not approved,
            "engine": output.extraction_source + " + document rules",
            "extraction_warning": output.extraction_warning,
            "passport_mode": os.environ.get("DOCUMENT_PASSPORT_MODE", "strict"),
            "extraction_only": document_type == "passport" and os.environ.get("DOCUMENT_PASSPORT_MODE") == "extraction",
            "trained_authenticity_model": False,
            "ocr_confidence": None,
            "word_count": word_count,
            "page_count": len(pages),
            "quality": metrics,
            "classification": classification,
            "checks": checks,
            "extracted_field_names": sorted(extracted),
            "reasons": list(dict.fromkeys(issues)),
            "authenticity_provider": authenticity.get("provider"),
            "message": "Automated document checks passed."
            if passed
            else "Automated checks were inconclusive. Review the guidance and try a clearer document.",
        }
        if passed and not approved:
            result["message"] += (
                " Official authenticity is not established; administrator review is required."
            )
        return {
            "outcome": "checks_passed" if passed else "inconclusive",
            "result": result,
            "extracted_data": seal_details(extracted),
            "document_status": "Approved" if approved else "Pending",
        }

    @staticmethod
    def check_authenticity(path, document_type):
        """Optional operator-configured HTTPS service; never infer authenticity locally."""
        url = os.environ.get("DOCUMENT_AUTHENTICITY_URL", "")
        if not url:
            return {"verified": False, "provider": None}
        if not url.startswith("https://"):
            return {"verified": False, "provider": None}
        import httpx
        from pydantic import BaseModel, ConfigDict, Field, StrictBool

        class TrustedResult(BaseModel):
            model_config = ConfigDict(extra="forbid")
            authenticity_verified: StrictBool
            provider: str = Field(min_length=1, max_length=100)
            reference: str = Field(min_length=1, max_length=200)

        try:
            with path.open("rb") as document:
                response = httpx.post(
                    url,
                    data={"document_type": document_type},
                    files={"document": ("document" + path.suffix, document)},
                    headers={
                        "Authorization": "Bearer "
                        + os.environ.get("DOCUMENT_AUTHENTICITY_API_KEY", "")
                    },
                    timeout=20,
                    follow_redirects=False,
                )
            response.raise_for_status()
            if len(response.content) > 16384:
                return {"verified": False, "provider": None}
            validated = TrustedResult.model_validate(response.json())
            return {
                "verified": validated.authenticity_verified,
                "provider": validated.provider,
            }
        except Exception:
            # Provider failures never produce approval, and response bodies are not logged.
            return {"verified": False, "provider": None}
