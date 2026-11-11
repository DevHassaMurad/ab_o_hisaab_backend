"""Registration schema rejects bypasses before creating database records."""

import unittest
from pydantic import ValidationError
from model.registration_schemas import RegistrationRequest
from tests.test_verification import registration_payload


class RegistrationSchemaTests(unittest.TestCase):
    def test_required_verification_and_privilege_fields(self):
        with self.assertRaises(ValidationError):
            RegistrationRequest.model_validate(registration_payload())
        data = {
            **registration_payload(),
            "verification_id": "a" * 36,
            "verification_token": "b" * 43,
        }
        self.assertEqual(RegistrationRequest.model_validate(data).user_type, "donor")
        for override in (
            {"role_id": 4},
            {"consent": False},
            {"password": "short"},
            {"confirm_password": "different-password"},
            {"identity_number": "wrong"},
        ):
            with self.assertRaises(ValidationError):
                RegistrationRequest.model_validate({**data, **override})
