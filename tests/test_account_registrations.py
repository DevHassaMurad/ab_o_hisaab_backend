import os,sys,importlib.util,unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1]
BACKEND=ROOT if (ROOT/'config/database.py').exists() else Path('D:/Aab_Hisaab/backend')
load_dotenv(BACKEND/'.env')
sys.path.insert(0,str(BACKEND))
os.environ['DISABLE_OUTBOX_WORKER']='1'
for name, file in [('controller.account_registrations','controller/account_registrations.py'),('routes.account_registration_routes','routes/account_registration_routes.py')]:
 spec=importlib.util.spec_from_file_location(name,ROOT/file); module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
from controller.account_registrations import AccountRegistrations
from routes.account_registration_routes import router
from config.database import Base,get_db
from controller.authentication import AuthFlowError,Authentication
from config.outbox import deliver_due
from config.auth_mail import MailDeliveryError
from model.user_model import User
from model.role_model import Role
from model.registration_model import PROFILE_MODELS
from model.document_model import Document
from model.donation_model import Ngo
from model.fund_model import EmailOutbox
from middlewares.auth_middleware import auth_middleware
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from pwdlib import PasswordHash

class ReviewTests(unittest.TestCase):
 def setUp(self):
  self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
  Base.metadata.create_all(self.engine)
  self.db=Session(self.engine)
  for i,name in enumerate(['UNASSIGNED','ADMIN','DONOR','NGO','SHOPKEEPER','HOUSEHOLDER','PDMA_OFFICER']):self.db.add(Role(id=i,name=name))
  self.admin=User(name='Admin',email='admin@example.org',password='unused',role_id=1,email_verified_at=datetime.utcnow())
  self.db.add(self.admin);self.db.commit()
  self.app=FastAPI();self.app.include_router(router,prefix='/api')
  @self.app.exception_handler(AuthFlowError)
  def handle(request,exc):return JSONResponse({'detail':exc.detail},status_code=exc.status)
  self.app.dependency_overrides[get_db]=lambda:self.db
  self.app.dependency_overrides[auth_middleware]=lambda:self.admin
  self.client=TestClient(self.app)
 def tearDown(self):self.db.close();self.engine.dispose()
 def create(self,kind='donor',certificate=True):
  u=User(name='Test '+kind,email=kind+'@example.org',password=PasswordHash.recommended().hash('test-password-123'),role_id=0,registration_status='under_review',email_verified_at=datetime.utcnow())
  self.db.add(u);self.db.flush()
  extra={'donor':{'donor_type':'international'},'ngo':{'organization_name':'Test NGO','registration_number':'REG-123','designation':'Director'},'shopkeeper':{'shop_name':'Shop','business_number':'B-123','business_category':'Food'},'householder':{'household_size':4,'dependents':2}}[kind]
  self.db.add(PROFILE_MODELS[kind](user_id=u.id,phone='+923001234567',identity_type='cnic',identity_number='1234512345671',country='PK',state='Sindh',city='Karachi',postal_code='74000',address1='Test street',**extra))
  types=[('cnic','cnicFront')]+([('ngo_registration','ngoCertificate')] if kind=='ngo' and certificate else [])
  for typ,slot in types:self.db.add(Document(user_id=u.id,user_type=kind,document_type=typ,document_slot=slot,document_url='test.png',content_type='image/png',size_bytes=100))
  self.db.commit();return u
 def test_four_types_approve_login_and_queue_once(self):
  for kind in PROFILE_MODELS:
   u=self.create(kind)
   self.assertEqual(Authentication(self.db).authenticate({'email':u.email,'password':'test-password-123'})[1],403)
   response=self.client.post(f'/api/admin/registrations/{u.id}/verify',json={'reviewed':True})
   self.assertEqual(response.status_code,200,response.text)
   self.assertTrue(response.json()['verified'])
   self.assertEqual(response.json()['role'],kind.upper())
   self.assertEqual(Authentication(self.db).authenticate({'email':u.email,'password':'test-password-123'})[1],200)
   self.client.post(f'/api/admin/registrations/{u.id}/verify',json={'reviewed':True})
   self.assertEqual(self.db.scalar(select(func.count()).select_from(EmailOutbox).where(EmailOutbox.recipient==u.email)),1)
   self.assertTrue(all(d.verified_by==self.admin.id for d in self.db.scalars(select(Document).where(Document.user_id==u.id))))
   if kind=='ngo':self.assertIsNotNone(self.db.scalar(select(Ngo).where(Ngo.user_id==u.id)))
 def test_block_missing_certificate_and_no_email_ownership_bypass(self):
  u=self.create('ngo',False)
  self.assertEqual(self.client.post(f'/api/admin/registrations/{u.id}/verify',json={'reviewed':True}).status_code,409)
  self.assertEqual(u.role_id,0)
  donor=self.create();donor.email_verified_at=None;self.db.commit()
  self.assertEqual(self.client.post(f'/api/admin/registrations/{donor.id}/verify',json={'reviewed':True}).status_code,200)
  self.assertEqual(Authentication(self.db).authenticate({'email':donor.email,'password':'test-password-123'})[0]['code'],'EMAIL_NOT_VERIFIED')
 def test_list_detail_access_and_no_secrets(self):
  u=self.create()
  response=self.client.get('/api/admin/registrations?user_type=donor&search=Test')
  self.assertEqual(response.json()['total'],1)
  self.assertEqual(response.headers['cache-control'],'no-store')
  detail=self.client.get(f'/api/admin/registrations/{u.id}').json()
  self.assertEqual(detail['profile']['identity_number'],'1234512345671')
  self.assertNotIn('password',str(detail));self.assertNotIn('extracted_data',str(detail))
  self.app.dependency_overrides[auth_middleware]=lambda:u
  self.assertEqual(self.client.get('/api/admin/registrations').status_code,403)
  self.assertEqual(self.client.post(f'/api/admin/registrations/{u.id}/verify',json={'reviewed':True}).status_code,403)
 def test_mail_failure_retries_without_undoing_verification(self):
  u=self.create();AccountRegistrations(self.db,self.admin).verify(u.id)
  from unittest.mock import Mock
  mailer=Mock();mailer.deliver.side_effect=MailDeliveryError('Unavailable')
  self.assertEqual(deliver_due(self.engine,mailer),0)
  self.db.expire_all()
  self.assertEqual(u.registration_status,'approved')
  row=self.db.scalar(select(EmailOutbox));self.assertEqual(row.status,'pending');self.assertEqual(row.attempts,1)
 def test_transaction_failure_leaves_account_pending(self):
  u=self.create()
  with patch('controller.account_registrations.enqueue',side_effect=RuntimeError('DB failure')):
   with self.assertRaises(RuntimeError):AccountRegistrations(self.db,self.admin).verify(u.id)
  self.db.refresh(u);self.assertEqual(u.role_id,0);self.assertEqual(u.registration_status,'under_review')
  self.assertEqual(self.db.scalar(select(Document.status).where(Document.user_id==u.id)),'Pending')

if __name__=='__main__':unittest.main()
