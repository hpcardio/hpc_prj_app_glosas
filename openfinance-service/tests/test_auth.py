import time
import jwt
import pytest
from sqlalchemy import create_engine,text
from openfinance.runtime import authenticate_with


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setenv('ALGORITHM','HS256'); monkeypatch.setenv('SECRET_KEY','k'*64)
    monkeypatch.setenv('AUTH_SCHEMA','api_prontocardio')
    engine=create_engine('sqlite://')
    with engine.begin() as c:
        c.exec_driver_sql("ATTACH DATABASE ':memory:' AS api_prontocardio")
        c.exec_driver_sql('CREATE TABLE api_prontocardio.usuarios_api (id int,email text,ativo boolean,perfil text,telas_permitidas text)')
        c.exec_driver_sql("INSERT INTO api_prontocardio.usuarios_api VALUES (1,'demo@example.test',true,'ti',null)")
    return authenticate_with(engine),engine


def token(**kw):
    return jwt.encode({'sub':'demo@example.test','exp':int(time.time())+60,**kw},'k'*64,algorithm='HS256')


def test_accepts_only_active_ti(auth):
    authenticate,engine=auth
    assert authenticate(token())==1
    with engine.begin() as c:c.exec_driver_sql("UPDATE api_prontocardio.usuarios_api SET perfil='usuario'")
    assert authenticate(token()) is None
    with engine.begin() as c:c.exec_driver_sql("UPDATE api_prontocardio.usuarios_api SET perfil='ti',ativo=false")
    assert authenticate(token()) is None


def test_rejects_expired_missing_exp_unknown_subject_or_signature(auth):
    authenticate,_=auth
    bad=[token(exp=1),token(sub='unknown@example.test'),jwt.encode({'sub':'demo@example.test'},'k'*64,algorithm='HS256'),
         jwt.encode({'sub':'demo@example.test','exp':int(time.time())+60},'other'*20,algorithm='HS256'),'invalid']
    assert all(authenticate(value) is None for value in bad)


def test_existing_financial_management_permission_allows_own_bank_screen(auth):
    authenticate,engine=auth
    with engine.begin() as c:
        c.execute(text('UPDATE api_prontocardio.usuarios_api SET perfil=:p,telas_permitidas=:s'),
                  {'p':'usuario','s':'["contas_pagar_gestao"]'})
    assert authenticate(token())==1
    with engine.begin() as c:
        c.execute(text('UPDATE api_prontocardio.usuarios_api SET telas_permitidas=:s'),{'s':'["contas_pagar_operacao"]'})
    assert authenticate(token()) is None


def test_legacy_tokens_require_trusted_frontend_and_expired_tokens_still_fail(auth):
    authenticate,_=auth
    legacy=jwt.encode({'sub':'demo@example.test'},'k'*64,algorithm='HS256')
    assert authenticate(legacy) is None
    assert authenticate(legacy,allow_legacy=True)==1
    assert authenticate(token(exp=1),allow_legacy=True) is None
