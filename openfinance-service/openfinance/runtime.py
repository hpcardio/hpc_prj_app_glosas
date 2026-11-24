import os
import re
import json
import jwt
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from .api import create_app
from .provider import Pluggy
from .store import Base

SCHEMA='prontoglosa_openfinance'


def resources():
    engine=create_engine(os.environ['OPENFINANCE_DATABASE_URL'],pool_pre_ping=True,pool_size=5,max_overflow=2,
                         connect_args={'options':f'-csearch_path={SCHEMA}','connect_timeout':10})
    return engine,sessionmaker(engine,expire_on_commit=False),Pluggy(os.environ['PLUGGY_CLIENT_ID'],os.environ.get('PLUGGY_CLIENT_SECRET',''))


def authenticate_with(engine):
    # Current API checks expiry, active account and password-bound token version.
    # No password hashes are granted to the restricted Open Finance DB role.
    import httpx
    algorithm=os.environ['ALGORITHM']
    if algorithm not in {'HS256','HS384','HS512'}: raise ValueError('Unsupported JWT algorithm')
    def authenticate(token):
        try:
            jwt.decode(token,os.environ['SECRET_KEY'],algorithms=[algorithm],
                       options={'require':['exp','iat','sub','sv']})
        except jwt.InvalidTokenError: return None
        try:
            response=httpx.get(os.environ.get('AUTH_API_URL','http://api-prontocardio-api_prontocardio-1:8000')+'/usuarios/me',
                               headers={'Authorization':'Bearer '+token},timeout=5,follow_redirects=False)
            if response.status_code in {401,403}: return None
            response.raise_for_status()
            user=response.json()
        except (httpx.HTTPError,ValueError) as exc:
            from sqlalchemy.exc import SQLAlchemyError
            raise SQLAlchemyError('Authoritative authentication unavailable') from exc
        if not isinstance(user,dict) or user.get('ativo') is not True:return None
        if user.get('perfil')=='ti' or 'contas_pagar_gestao' in (user.get('telas_permitidas') or []):
            return user.get('id')
        return None
    return authenticate


def get_app():
    engine,sf,provider=resources()
    return create_app(sf,provider,os.environ['WEBHOOK_SECRET'],authenticate_with(engine),
                      sandbox_only=os.environ.get('SANDBOX_ONLY','true').lower()!='false',
                      frontend_secret=os.environ.get('PRONTOGLOSA_FRONTEND_KEY',''))


if __name__=='__main__':
    # Deliberately explicit migration; web requests never perform DDL.
    engine,_,_=resources()
    Base.metadata.create_all(engine)
