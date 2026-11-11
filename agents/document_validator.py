"""Document-specific structural checks; matching text is never proof of authenticity."""

import re
import os


def mrz_check(value):
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return str(
        sum(
            (0 if char == "<" else alphabet.index(char)) * [7, 3, 1][i % 3]
            for i, char in enumerate(value)
        )
        % 10
    )


def validate_mrz(text):
    lines = [re.sub(r"\s", "", line.upper()) for line in text.splitlines()]
    for index, line in enumerate(lines[:-1]):
        second = lines[index + 1]
        if (
            len(line) == len(second) == 44
            and line.startswith("P<")
            and re.fullmatch(r"[A-Z0-9<]{44}", second)
        ):
            checks = [
                (second[:9], second[9]),
                (second[13:19], second[19]),
                (second[21:27], second[27]),
            ]
            valid = all(
                check.isdigit() and mrz_check(value) == check for value, check in checks
            )
            # TD3 composite check includes optional-data field and its check digit.
            valid = (
                valid
                and second[43].isdigit()
                and mrz_check(second[:10] + second[13:20] + second[21:43]) == second[43]
            )
            return {
                "present": True,
                "valid": valid,
                "passport_number": second[:9].replace("<", ""),
            }
    return {"present": False, "valid": None}


def validate_content(kind, text, classification):
    lower = text.lower()
    issues, extracted, checks = [], {}, {}
    evidence = classification["evidence_counts"].get(kind, 0)
    if evidence < 2:
        issues.append(
            "The selected document type could not be established from readable content."
        )
    if kind == "cnic":
        number = re.search(r"\b([0-9]{5})[- ]?([0-9]{7})[- ]?([0-9])\b", text)
        checks["identity_number_readable"] = bool(number)
        checks["name_field_present"] = bool(
            re.search(r"\b(name|father|husband)\b", lower)
        )
        if number:
            extracted["identity_number"] = "".join(number.groups())
        if not number or not checks["name_field_present"]:
            issues.append("CNIC number and name-related fields must be readable.")
    elif kind == "passport":
        mrz = validate_mrz(text)
        checks["mrz_present"], checks["mrz_checks_passed"] = (
            mrz["present"],
            mrz["valid"],
        )
        if mrz.get("passport_number"):
            extracted["identity_number"] = mrz["passport_number"]
        else:
            number = re.search(
                r"passport\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9]{5,12})", text, re.I
            )
            if number:
                extracted["identity_number"] = number.group(1).upper()
        checks["identity_number_readable"] = "identity_number" in extracted
        checks["identity_fields_present"] = bool(
            re.search(r"surname|given names?|date of birth|nationality", lower)
        )
        if (
            not checks["identity_number_readable"]
            or not checks["identity_fields_present"]
        ):
            issues.append("Passport number and identity fields must be readable.")
        strict = os.environ.get("DOCUMENT_PASSPORT_MODE", "strict") != "extraction"
        checks["strict_passport_checks"] = strict
        if strict and mrz["present"] and not mrz["valid"]:
            issues.append("The readable passport MRZ check digits do not match.")
        if strict and not mrz["present"]:
            issues.append(
                "A complete passport MRZ was not readable; manual review is required."
            )
    elif kind == "home_address":
        address = re.search(
            r"(?:address|premises|service location)\s*[:\-]?\s*(.{10,180})", text, re.I
        )
        checks["address_readable"] = bool(address)
        checks["document_category_present"] = bool(
            re.search(
                r"utility|bill|tenancy|lease|electricity|gas|water|bank statement",
                lower,
            )
        )
        if address:
            extracted["address"] = address.group(1).strip()
        if not all(checks.values()):
            issues.append("A readable address and address-proof category are required.")
    elif kind == "ngo_registration":
        number = re.search(
            r"(?:registration|certificate)\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9/\-]{3,50})",
            text,
            re.I,
        )
        organization = re.search(
            r"(?:organi[sz]ation|society|trust)\s*(?:name)?\s*[:\-]\s*(.{3,150})",
            text,
            re.I,
        )
        checks["registration_number_readable"] = bool(number)
        checks["organization_readable"] = bool(organization)
        checks["certificate_heading_present"] = "certificate" in lower and (
            "registered" in lower or "registration" in lower
        )
        if number:
            extracted["registration_number"] = number.group(1)
        if organization:
            extracted["organization_name"] = organization.group(1).strip()
        if not all(checks.values()):
            issues.append(
                "Organization name, registration number and certificate heading must be readable."
            )
    return checks, extracted, issues
