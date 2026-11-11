import unittest
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config.auth_mail import MailDeliveryError, render_email
from config.database import Base, get_db
from controller.authentication import Authentication, AuthFlowError, now_utc
from main import app
from config.jwt_handler import JwtHandler
from middlewares.auth_middleware import auth_middleware
from model.auth_challenge_model import AuthChallenge
from model.role_model import Role
from model.user_model import User
from routes.auth_routes import _buckets


class FakeMailer:
    def __init__(self):
        self.messages = []
        self.fail = False

    def ensure_configured(self):
        pass

    def send(self, recipient, name, purpose, code=None):
        if self.fail:
            raise MailDeliveryError('Provider offline')
        self.messages.append((purpose, code))


class AuthFlowTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {'JWT_SECRET_KEY': 'test-secret-with-more-than-32-characters'})
        self.env.start()
        self.jwt_patch = patch.object(auth_middleware, 'jwt_handler', JwtHandler())
        self.jwt_patch.start()
        _buckets.clear()
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        self.db = Session()
        self.db.add_all([Role(id=0, name='UNASSIGNED'), Role(id=4, name='ADMIN')])
        self.db.flush()
        self.user = User(name='Test <Admin>', email='admin@example.org', role_id=4,
                         password=PasswordHash.recommended().hash('original-password'), email_verified_at=now_utc())
        self.db.add(self.user)
        self.db.commit()
        self.mail = FakeMailer()
        self.flow = Authentication(self.db, self.mail)
        app.dependency_overrides[get_db] = lambda: self.db
        self.mail_patch = patch('controller.authentication.AuthMailer', return_value=self.mail)
        self.mail_patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.mail_patch.stop()
        self.db.close()
        self.engine.dispose()
        self.env.stop()
        self.jwt_patch.stop()

    def code(self, purpose='password_reset'):
        self.flow.request_code(self.user.email, purpose)
        return self.mail.messages[-1][1]

    def test_code_is_six_digits_and_only_hmac_is_stored(self):
        code = self.code()
        challenge = self.flow.challenge(self.user, 'password_reset')
        self.assertRegex(code, r'^\d{6}$')
        self.assertNotEqual(challenge.code_hash, code)
        self.assertEqual(len(challenge.code_hash), 64)
        self.assertLess(challenge.expires_at - now_utc(), timedelta(minutes=11))

    def test_request_response_does_not_disclose_account_and_resend_is_limited(self):
        known = self.flow.request_code(self.user.email, 'password_reset')
        unknown = self.flow.request_code('missing@example.org', 'password_reset')
        duplicate = self.flow.request_code(self.user.email, 'password_reset')
        self.assertEqual(known, unknown)
        self.assertEqual(known, duplicate)
        self.assertEqual(len(self.mail.messages), 1)

    def test_five_failed_attempts_lock_the_code(self):
        code = self.code()
        wrong = '111111' if code != '111111' else '222222'
        for _ in range(5):
            with self.assertRaises(AuthFlowError):
                self.flow.verify_code(self.user.email, 'password_reset', wrong)
        with self.assertRaises(AuthFlowError):
            self.flow.verify_code(self.user.email, 'password_reset', code)

    def test_expired_code_is_rejected(self):
        code = self.code()
        self.flow.challenge(self.user, 'password_reset').expires_at = now_utc() - timedelta(seconds=1)
        self.db.commit()
        with self.assertRaises(AuthFlowError):
            self.flow.verify_code(self.user.email, 'password_reset', code)

    def test_email_confirmation_is_required_and_single_use(self):
        self.user.email_verified_at = None
        self.db.commit()
        login = self.client.post('/api/auth/authenticate', json={'email':self.user.email, 'password':'original-password'})
        self.assertEqual(login.status_code, 403)
        self.assertEqual(login.json()['code'], 'EMAIL_NOT_VERIFIED')
        code = self.code('email_confirmation')
        result = self.client.post('/api/auth/email-verification/verify', json={'email':self.user.email, 'code':code})
        self.assertEqual(result.status_code, 200)
        self.assertIsNotNone(self.user.email_verified_at)
        with self.assertRaises(AuthFlowError):
            self.flow.verify_code(self.user.email, 'email_confirmation', code)
        self.assertEqual(self.client.post('/api/auth/authenticate', json={'email':self.user.email, 'password':'original-password'}).status_code, 200)

    def test_complete_reset_revokes_sessions_and_rejects_replay(self):
        token = self.client.post('/api/v1/auth/token', data={'username':self.user.email,'password':'original-password'}).json()['access_token']
        headers = {'Authorization':f'Bearer {token}'}
        self.assertEqual(self.client.get('/api/v1/auth/me', headers=headers).status_code, 200)
        code = self.code()
        grant = self.flow.verify_code(self.user.email, 'password_reset', code)['reset_token']
        with self.assertRaises(AuthFlowError):
            self.flow.verify_code(self.user.email, 'password_reset', code)
        self.flow.reset_password(self.user.email, grant, 'replacement-password')
        self.assertEqual(self.client.get('/api/auth/protected', headers=headers).status_code, 401)
        with self.assertRaises(AuthFlowError):
            self.flow.reset_password(self.user.email, grant, 'another-password')
        self.assertTrue(PasswordHash.recommended().verify('replacement-password', self.user.password))
        self.assertEqual(self.mail.messages[-1][0], 'password_changed')

    def test_resend_invalidates_previous_code_and_reset_grant(self):
        old_code = self.code()
        old_grant = self.flow.verify_code(self.user.email, 'password_reset', old_code)['reset_token']
        self.flow.challenge(self.user, 'password_reset').sent_at -= timedelta(seconds=61)
        self.db.commit()
        self.code()
        with self.assertRaises(AuthFlowError):
            self.flow.reset_password(self.user.email, old_grant, 'replacement-password')

    def test_wrong_purpose_and_expired_grant_are_rejected(self):
        code = self.code()
        with self.assertRaises(AuthFlowError):
            self.flow.verify_code(self.user.email, 'email_confirmation', code)
        grant = self.flow.verify_code(self.user.email, 'password_reset', code)['reset_token']
        self.flow.challenge(self.user, 'password_reset').reset_expires_at = now_utc() - timedelta(seconds=1)
        self.db.commit()
        with self.assertRaises(AuthFlowError):
            self.flow.reset_password(self.user.email, grant, 'replacement-password')

    def test_password_change_requires_current_password_and_revokes_sessions(self):
        token = self.client.post('/api/auth/token', data={'username':self.user.email,'password':'original-password'}).json()['access_token']
        headers = {'Authorization':f'Bearer {token}'}
        bad = self.client.post('/api/auth/password', headers=headers, json={'current_password':'wrong','new_password':'replacement-password'})
        self.assertEqual(bad.json()['code'], 'WRONG_CURRENT_PASSWORD')
        good = self.client.post('/api/auth/password', headers=headers, json={'current_password':'original-password','new_password':'replacement-password'})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(self.client.get('/api/auth/me', headers=headers).status_code, 401)

    def test_smtp_failure_does_not_persist_new_code(self):
        self.mail.fail = True
        self.flow.request_code(self.user.email, 'password_reset')
        self.assertIsNone(self.db.scalar(select(AuthChallenge)))

    def test_route_validation_and_unknown_account_are_safe(self):
        self.assertEqual(self.client.post('/api/auth/password-reset/verify', json={'email':self.user.email,'code':'12345'}).status_code, 422)
        self.assertEqual(self.client.post('/api/auth/password-reset/request', json={'email':'invalid'}).status_code, 422)
        self.assertEqual(self.client.post('/api/auth/password-reset/request', json={'email':'missing@example.org'}).status_code, 202)
        self.assertEqual(self.client.post('/api/auth/password-reset/confirm', json={
            'email':self.user.email,'reset_token':'x'*43,'new_password':'replacement-password','confirm_password':'different-password',
        }).status_code, 422)

    def test_email_template_escapes_names_and_has_text_alternative(self):
        title, text, html = render_email('<script>alert(1)</script>', 'password_reset', '012345')
        self.assertIn('012345', text)
        self.assertIn('012345', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('10 minutes', text)

    def test_registration_sends_confirmation_without_accepting_a_privileged_role(self):
        response = self.client.post('/api/auth/register', json={
            'email':'new@example.org', 'user_name':'New User', 'password':'new-account-password', 'role_id':4,
        })
        self.assertEqual(response.status_code, 422)
        self.assertIsNone(self.db.scalar(select(User).where(User.email == 'new@example.org')))

    def test_logout_revokes_the_issued_access_token(self):
        token = self.client.post('/api/auth/token', data={'username':self.user.email,'password':'original-password'}).json()['access_token']
        headers = {'Authorization':f'Bearer {token}'}
        self.assertEqual(self.client.post('/api/auth/logout', headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/api/auth/me', headers=headers).status_code, 401)


if __name__ == '__main__':
    unittest.main()
