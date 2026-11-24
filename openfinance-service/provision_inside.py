"""Run inside existing API container; stdout is captured privately by deploy script."""
import json
import secrets
from sqlalchemy import text
from app_prontocardio.database import postgres_engine
from app_prontocardio.settings import Settings

settings=Settings()
role='prontoglosa_openfinance'
password=secrets.token_urlsafe(48)
with postgres_engine.begin() as c:
    if c.scalar(text('SELECT 1 FROM pg_roles WHERE rolname=:r'),{'r':role}):
        raise RuntimeError('Role already exists; refusing credential rotation')
    c.exec_driver_sql(f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")
    c.exec_driver_sql(f'CREATE SCHEMA {role} AUTHORIZATION {role}')
    schema=postgres_engine.dialect.identifier_preparer.quote(settings.POSTGRES_SCHEMA)
    c.exec_driver_sql(f'GRANT USAGE ON SCHEMA {schema} TO {role}')
    c.exec_driver_sql(f'GRANT SELECT (id,email,ativo,perfil,telas_permitidas) ON {schema}.usuarios_api TO {role}')
url=postgres_engine.url.set(username=role,password=password)
print(json.dumps({'OPENFINANCE_DATABASE_URL':url.render_as_string(hide_password=False),
                  'SECRET_KEY':settings.SECRET_KEY,'ALGORITHM':settings.ALGORITHM,
                  'AUTH_SCHEMA':settings.POSTGRES_SCHEMA,
                  'PLUGGY_CLIENT_ID':'a4fe9375-49bd-468c-9473-f86491b5fb04',
                  'PLUGGY_CLIENT_SECRET':'','WEBHOOK_SECRET':secrets.token_urlsafe(48),
                  'PRONTOGLOSA_FRONTEND_KEY':secrets.token_urlsafe(48),'SANDBOX_ONLY':'true'}))
