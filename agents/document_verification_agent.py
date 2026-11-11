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
from agents.document_preprocessor import DocumentPreprocessor, decode_pages
from agents.document_validator import validate_content
from agents.ocr_service import OCRService

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
        ocr = OCRService()
        prepared, metrics, issues = [], [], []
        preprocessor = DocumentPreprocessor()
        for page in pages:
            enhanced, quality, problems = preprocessor.prepare(
                page, ocr.orientation(page)
            )
            prepared.append(enhanced)
            metrics.append(quality)
            issues.extend(problems)
        progress("enhancing", True)
        progress("extracting", False)
        outputs = [ocr.extract(page) for page in prepared]
        text = "\n".join(output["text"] for output in outputs)
        word_count = sum(output["word_count"] for output in outputs)
        confidence = sum(
            output["confidence"] * output["word_count"] for output in outputs
        ) / max(1, word_count)
        progress("extracting", True)
        progress("validating", False)
        classification = classify(text)
        checks, extracted, validation_issues = validate_content(
            document_type, text, classification
        )
        issues.extend(validation_issues)
        if word_count < 12:
            issues.append("Not enough document text is readable for automated checks.")
        if confidence < float(os.environ.get("DOCUMENT_MIN_OCR_CONFIDENCE", "45")):
            issues.append("OCR confidence is too low; upload a clearer document.")
        checks["layout_has_multiple_lines"] = (
            sum(output["line_count"] for output in outputs) >= 3
        )
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
            and authenticity["verified"]
            and os.environ.get("DOCUMENT_AUTO_APPROVE", "false").lower() == "true"
        )
        progress("validating", True)
        progress("completing", False)
        result = {
            "automated_checks_passed": passed,
            "authenticity_verified": bool(authenticity["verified"]),
            "manual_review_required": not approved,
            "engine": "OpenCV + Tesseract LSTM + document rules",
            "trained_authenticity_model": False,
            "ocr_confidence": round(confidence, 2),
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
