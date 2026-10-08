import os, subprocess, sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote
root=Path(__file__).resolve().parents[5]
name=os.environ.get('UI6_DATABASE','hpagent_ui6_review_20261008')
env=os.environ.copy()
for key,service in [('MIGRATION_DATABASE_URL','hpagent_web-app-postgres-1'),('APP_DATABASE_URL','hpagent_web-hpagent-api-1'),('WORKER_DATABASE_URL','hpagent_web-hpagent-1')]:
    if key=='MIGRATION_DATABASE_URL':
        password=subprocess.check_output(['docker','exec',service,'printenv','POSTGRES_PASSWORD'],text=True).strip()
        value=f'postgresql://hpagent_migrate:{quote(password,safe="")}@localhost:5434/{name}'
    else:
        value=subprocess.check_output(['docker','exec',service,'printenv',key],text=True).strip()
        parsed=urlsplit(value)
        value=urlunsplit((parsed.scheme,parsed.netloc.split('@')[0]+'@localhost:5434','/'+name,parsed.query,''))
    env[key]=value
env.update(HPAGENT_MIGRATIONS_DIR=str(root/'persistence/migrations'),REDIS_URL='redis://localhost:6379/12',PYTHONPATH=str(root/'src'),WEB_API_PORT='8186',WEB_DEV_PORT='5279',FILE_STORE_ROOT='/tmp/hpagent-ui6-files-20261008',HPAGENT_UI5_EVIDENCE_DIR=str(root/'artifacts/product-acceptance/ui-5/self-review/screenshots'))
if sys.argv[1]=='contract':
    env['HPAGENT_ENV']='test'
    code=subprocess.call([str(root/'.venv/bin/python'),'-m','pytest',*sys.argv[2:]],cwd=root,env=env,start_new_session=True)
else:
    os.chdir(root/'web')
    os.execvpe('node', ['node', str(root/'web/node_modules/@playwright/test/cli.js'), 'test', *sys.argv[1:]], env)
raise SystemExit(code)
