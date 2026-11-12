from typing import Literal
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from config.database import get_db
from middlewares.role_middleware import require_roles
from controller.account_registrations import AccountRegistrations

router = APIRouter(prefix='/admin/registrations', tags=['Account registrations'])
admin_only = require_roles('ADMIN')
Kind = Literal['donor', 'ngo', 'shopkeeper', 'householder']

class VerifyAccount(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reviewed: Literal[True]

@router.get('')
def listing(response: Response, user_type: Kind = 'donor', search: str = Query('', max_length=100), status: Literal['', 'pending', 'verified'] = '', page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user=Depends(admin_only), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return AccountRegistrations(db, user).listing(user_type, search.strip(), status, page, page_size)

@router.get('/{user_id}')
def detail(user_id: int, response: Response, user=Depends(admin_only), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return AccountRegistrations(db, user).detail(user_id)

@router.post('/{user_id}/verify')
def verify(user_id: int, data: VerifyAccount, response: Response, user=Depends(admin_only), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return AccountRegistrations(db, user).verify(user_id)
