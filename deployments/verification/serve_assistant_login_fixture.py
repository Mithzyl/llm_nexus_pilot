"""Run real Nexus and Assistant login locally with isolated synthetic accounts.

From repository root: PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python
deployments/verification/serve_assistant_login_fixture.py. Requires existing local MySQL/MinIO,
Assistant checkout and its venv/node dependencies. Ports18011/18013/3117/3118 must be free.
No model calls, original databases or credentials copied. Ctrl-C stops owned processes only;
synthetic databases and private login file are retained, never automatically deleted.
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

from serve_identity_memory_fixture import configure, prepare_database, seed_login_application, seed_model
from mcp_browser_fixture import seed_user


def main():
    """Own four temporary services; use existing authentication without touching normal runtime."""
    for port in (18011,18013,3117,3118):
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            check.bind(("127.0.0.1",port))
    root,directory,name,api_key=configure()
    assistant=Path("/Users/mith/Desktop/project/superapp-next")
    app_token=secrets.token_urlsafe(32)
    os.environ["NEXUSPILOT_REGISTRATION_ENABLED"]="false"
    os.environ["NEXUSPILOT_APP_TOOLS_JSON"]=json.dumps({"personal_assistant":{
        "inbound_key":app_token,"service_token":secrets.token_urlsafe(32),
        "base_url":"http://127.0.0.1:18013","allow_local_http":True,
        "operations":{"personal_assistant_search_notes":"/internal/nexus/tools"},
        "login_redirect_uris":["http://127.0.0.1:3118/nexus-callback"]}})
    asyncio.run(prepare_database(name))
    subprocess.run([sys.executable,"-m","alembic","upgrade","head"],cwd=root/"apps/api",check=True)
    asyncio.run(seed_model())
    asyncio.run(seed_user(directory,user_id="login-fixture-user",email="login-fixture@example.test",
                          display_name="Nexus Login Fixture",filename="nexus-login.json"))
    asyncio.run(seed_login_application(directory))
    clean={k:v for k,v in os.environ.items() if not k.startswith(("NEXUSPILOT_","ASSISTANT_"))}
    pa_env={**clean,"PYTHONPATH":str(assistant/"services/assistant-api/src"),
        "ASSISTANT_ENVIRONMENT":"development",
        "ASSISTANT_DATABASE_URL":f"sqlite+aiosqlite:///{directory}/assistant.sqlite",
        "ASSISTANT_ALLOW_SCHEMA_CREATE":"true","ASSISTANT_NEXUS_LOGIN_ENABLED":"true",
        "ASSISTANT_NEXUS_BASE_URL":"http://127.0.0.1:18011",
        "ASSISTANT_NEXUS_WEB_URL":"http://127.0.0.1:3117",
        "ASSISTANT_NEXUS_LOGIN_CALLBACK_URL":"http://127.0.0.1:3118/nexus-callback",
        "ASSISTANT_NEXUS_APPLICATION_TOKEN":app_token,
        "ASSISTANT_TOOL_CALLBACK_TOKEN":secrets.token_urlsafe(32),
        "ASSISTANT_COMMUTE_PROVIDER_MODE":"mock","ASSISTANT_REGISTER_ENABLED":"false"}
    password=secrets.token_urlsafe(24)
    with os.fdopen(os.open(directory/"assistant-login.json",os.O_WRONLY|os.O_CREAT,0o600),"w") as output:
        json.dump({"email":"assistant-fixture@example.test","password":password},output)
    pa_python=str(assistant/"services/assistant-api/.venv/bin/python")
    subprocess.run([pa_python,"-c",'''import asyncio,os
from assistant_api.core.config import Settings
from assistant_api.infrastructure.database import create_engine_and_factory
from assistant_api.models import Base,User
from assistant_api.core.security import hash_password
async def seed():
    engine,factory=create_engine_and_factory(Settings().database_url)
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add(User(user_id="synthetic-app-account",email="assistant-fixture@example.test",display_name="Assistant Login Fixture",password_verifier=hash_password(os.environ["FIXTURE_PASSWORD"]),status="active"))
        await db.commit()
    await engine.dispose()
asyncio.run(seed())'''],env={**pa_env,"FIXTURE_PASSWORD":password},check=True)
    children=[]
    logs=[]
    try:
        commands=[
            ([sys.executable,"-m","uvicorn","nexuspilot_api.main:app","--host","127.0.0.1","--port","18011"],root,dict(os.environ)),
            ([pa_python,"-m","uvicorn","assistant_api.main:create_app","--factory","--host","127.0.0.1","--port","18013"],assistant,pa_env),
            (["npm","run","dev","--","--hostname","127.0.0.1","--port","3117"],root/"apps/web",{**clean,"NEXUSPILOT_API_URL":"http://127.0.0.1:18011","NEXUSPILOT_API_KEY":api_key}),
            (["npm","run","dev","--","--hostname","127.0.0.1","--port","3118"],assistant/"apps/web",{**clean,"PA_API_URL":"http://127.0.0.1:18013"}),
        ]
        for index,(command,cwd,env) in enumerate(commands):
            log=os.fdopen(os.open(directory/f"service-{index}.log",os.O_WRONLY|os.O_CREAT,0o600),"w")
            logs.append(log)
            children.append(subprocess.Popen(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT))
        print(f"Fixture database={name}; private directory={directory}",flush=True)
        while all(child.poll() is None for child in children): time.sleep(1)
        raise RuntimeError("Fixture service exited; inspect private service logs")
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None: child.terminate()
        for child in children:
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        for log in logs: log.close()


if __name__=="__main__":
    main()
