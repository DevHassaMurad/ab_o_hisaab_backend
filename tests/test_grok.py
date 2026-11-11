import os
import unittest
from unittest.mock import patch
from agents.document_classifier import classify
from agents.document_validator import validate_content
from tests.test_verification import VerificationTests, registration_payload
from model.verification_model import VerificationProcess

class PassportTests(unittest.TestCase):
    def test_dummy_extraction_and_strict(self):
        text = 'DUMMY PASSPORT\nPassport number: AB1234567\nSurname: TEST\nNationality: Pakistani\nDate of birth: 1990-01-01'
        for mode, passes in [('extraction', True), ('strict', False)]:
            with patch.dict(os.environ, {'DOCUMENT_PASSPORT_MODE':mode}):
                checks, fields, issues = validate_content('passport', text, classify(text))
                self.assertEqual(not issues, passes)
                self.assertEqual(fields['identity_number'], 'AB1234567')

class NgoTests(VerificationTests):
    def test_two_required_documents(self):
        identity = self.upload(kind='ngo').json()
        certificate = self.upload(kind='ngo', document_type='ngo_registration').json()
        for item in (identity, certificate):
            row = self.db.get(VerificationProcess, item['id'])
            row.stage, row.outcome = 'completed', 'checks_passed'
        self.db.commit()
        payload = {**registration_payload('ngo'), 'verification_id':identity['id'], 'verification_token':identity['verification_token']}
        self.assertEqual(self.client.post('/api/auth/register', json=payload).status_code, 422)
        payload.update(ngo_verification_id=certificate['id'], ngo_verification_token=certificate['verification_token'])
        response = self.client.post('/api/auth/register', json=payload)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(len(response.json()['documents']), 2)
        self.assertTrue(all(self.db.get(VerificationProcess, item['id']).consumed_at for item in (identity, certificate)))
