"""Prepare, serve and verify the local assistant-api with NexusPilot/OpenCode DeepSeek.

Use superapp-next/services/assistant-api/.venv/bin/python and its src in PYTHONPATH.
Modes: prepare writes private local config and synthetic accounts; serve starts
the unmodified assistant-api on 8010; verify sends exactly two paid Agent asks.
NexusPilot must separately run on 8001 with its apps/api/.env configuration.
Private state and SQLite live in a chmod-700 temporary directory, not reports.
No user content or credentials are emitted. No deployment or Git writes occur.
"""

import argparse
import asyncio
import json
import secrets
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values, set_key
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = Path("/private/tmp/nexus-personal-assistant-20261023")
STATE = PRIVATE / "state.json"
PROVIDER = "opencode"
MODEL = "deepseek-v4.1-flash"
TOOLS = (
    "personal_assistant_list_tasks",
    "personal_assistant_search_notes",
    "personal_assistant_list_calendar_events",
)


def private_state():
    """Load private integration credentials without printing or including them in reports."""
    return json.loads(STATE.read_text())


def settings_for(state):
    """Configure the actual assistant service, leaving existing App data/config untouched."""
    from assistant_api.core.config import Settings

    return Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///" + str(PRIVATE / "app.sqlite"),
        allow_schema_create=True,
        nexus_base_url="http://127.0.0.1:8001",
        nexus_application_token=state["inbound_key"],
        tool_callback_token=state["service_token"],
        nexus_role_bindings_json=json.dumps(
            {"controller": {"provider": PROVIDER, "model": MODEL}}
        ),
    )


def configure(state):
    """Register current envelope paths, preserving existing private keys and other Apps."""
    env_path = ROOT / "apps/api/.env"
    current = json.loads(
        dotenv_values(env_path).get("NEXUSPILOT_APP_TOOLS_JSON") or "{}"
    )
    old = current.get("personal_assistant")
    if old and old["inbound_key"] != state["inbound_key"]:
        raise RuntimeError("Existing PA configuration belongs to another setup")
    current["personal_assistant"] = {
        "inbound_key": state["inbound_key"],
        "service_token": state["service_token"],
        "base_url": "http://127.0.0.1:8010",
        "allow_local_http": True,
        "operations": {name: "/internal/nexus/tools" for name in TOOLS},
    }
    set_key(str(env_path), "NEXUSPILOT_APP_TOOLS_JSON", json.dumps(current))


async def prepare():
    """Generate server-only configuration and two synthetic App users, never resetting old users."""
    from assistant_api.core.security import hash_password
    from assistant_api.infrastructure.database import create_engine_and_factory
    from assistant_api.models import Base, Note, TaskItem, User

    if PRIVATE.exists():
        raise RuntimeError(
            "Private integration directory already exists; do not overwrite"
        )
    PRIVATE.mkdir(mode=0o700)
    state = {
        "inbound_key": secrets.token_urlsafe(40),
        "service_token": secrets.token_urlsafe(40),
        "accounts": [],
    }
    config = settings_for(state)
    engine, factory = create_engine_and_factory(config.database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db:
            for index in range(2):
                password = secrets.token_urlsafe(32)
                user = User(
                    email=f"pa-integration-{uuid4()}@example.test",
                    display_name="Integration",
                    password_verifier=hash_password(password),
                    status="active",
                )
                db.add(user)
                await db.flush()
                db.add(
                    TaskItem(
                        user_id=user.user_id,
                        title=f"Synthetic task {index}",
                        status="open",
                    )
                )
                db.add(
                    Note(
                        user_id=user.user_id,
                        title="Synthetic integration note",
                        body="Synthetic content only.",
                        tags_json=[],
                    )
                )
                state["accounts"].append(
                    {"user_id": user.user_id, "email": user.email, "password": password}
                )
            await db.commit()
        STATE.write_text(json.dumps(state))
        STATE.chmod(0o600)
        configure(state)
        print(
            "Private PA config and two synthetic accounts prepared; no credentials printed"
        )
    finally:
        await engine.dispose()


async def verify(report_path):
    """Exercise logged-in App asks, inspect Nexus events/results, and report metadata only."""
    from assistant_api.infrastructure.database import create_engine_and_factory
    from assistant_api.models import ToolCallReceipt

    if report_path.exists():
        raise RuntimeError("Report already exists")
    state = private_state()
    report = {
        "provider": PROVIDER,
        "model": MODEL,
        "runs": [],
        "checks": [],
        "status": "running",
    }
    engine, factory = create_engine_and_factory(settings_for(state).database_url)
    async with httpx.AsyncClient(timeout=35, trust_env=False) as http:

        async def request(method, url, expected=200, **kwargs):
            """Reject HTTP failures without exposing response bodies or authentication fields."""
            result = await http.request(method, url, **kwargs)
            if result.status_code != expected:
                raise RuntimeError(f"HTTP {result.status_code}; expected {expected}")
            return result

        try:
            logins = []
            for account in state["accounts"]:
                response = await request(
                    "POST",
                    "http://127.0.0.1:8010/api/v1/auth/login",
                    json={"email": account["email"], "password": account["password"]},
                )
                logins.append({"Authorization": "Bearer " + response.json()["token"]})
            headers = {
                "Authorization": "Bearer " + state["inbound_key"],
                "X-Application-Id": "personal_assistant",
                "X-External-User-Id": state["accounts"][0]["user_id"],
            }
            foreign = {**headers, "X-External-User-Id": state["accounts"][1]["user_id"]}
            for mode, prompt in [
                ("plain", "请用一句话解释什么是任务清单。不调用工具。"),
                (
                    "app_tools",
                    "请读取我的任务、搜索含Synthetic的笔记，以及2026-10-23到2026-10-24的日历。"
                    "使用已登记的personal_assistant工具，可先describe_tools加载，按工具结果简短总结，"
                    "空列表就明确说没有记录，不要猜测。",
                ),
            ]:
                started = time.monotonic()
                async with factory() as db:
                    prior_receipts = set(
                        await db.scalars(select(ToolCallReceipt.tool_call_id))
                    )
                ask = (
                    await request(
                        "POST",
                        "http://127.0.0.1:8010/api/v1/asks",
                        expected=202,
                        headers=logins[0],
                        json={"text": prompt, "idempotency_key": str(uuid4())},
                    )
                ).json()
                item = {
                    "mode": mode,
                    "local_run_id": ask["local_run_id"],
                    "run_id": ask["nexus_run_id"],
                }
                report["runs"].append(item)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                )
                print(
                    json.dumps({"started": mode, "run_id": item["run_id"]}), flush=True
                )
                for _ in range(240):
                    result = (
                        await request(
                            "GET",
                            "http://127.0.0.1:8010/api/v1/asks/" + ask["local_run_id"],
                            headers=logins[0],
                        )
                    ).json()
                    if result["status"] in {
                        "succeeded",
                        "failed",
                        "cancelled",
                        "unknown",
                    }:
                        break
                    await asyncio.sleep(2)
                item.update(
                    elapsed_seconds=round(time.monotonic() - started, 3),
                    app_status=result["status"],
                    workflow_id=result["workflow_execution_id"],
                    app_answer_exists=bool(result.get("answer")),
                )
                if not item["workflow_id"]:
                    raise RuntimeError("App did not retain workflow identity")
                path = (
                    "http://127.0.0.1:8001/api/v1/app/agent-workflows/"
                    + item["workflow_id"]
                )
                workflow = (
                    await request("GET", path + "/result", headers=headers)
                ).json()
                item.update(
                    status=workflow["status"],
                    final_text_exists=bool(
                        (workflow.get("final_output") or {}).get("final_text")
                    ),
                    usage=workflow.get("aggregate_usage"),
                    error_code=(workflow.get("error") or {}).get("error_code"),
                )
                events = (await request("GET", path + "/events", headers=headers)).text
                ids = [
                    int(line[4:])
                    for line in events.splitlines()
                    if line.startswith("id: ")
                ]
                item["event_count"] = len(ids)
                assert ids and ids == sorted(set(ids))
                midpoint = ids[len(ids) // 2]
                suffix = (
                    await request(
                        "GET",
                        path + "/events",
                        headers={**headers, "Last-Event-ID": str(midpoint)},
                    )
                ).text
                suffix_ids = [
                    int(line[4:])
                    for line in suffix.splitlines()
                    if line.startswith("id: ")
                ]
                assert suffix_ids == [number for number in ids if number > midpoint]
                item["event_suffix_replay_correct"] = True
                tail = (
                    await request(
                        "GET",
                        path + "/events",
                        headers={**headers, "Last-Event-ID": str(ids[-1])},
                    )
                ).text
                assert not tail.strip()
                await request("GET", path + "/result", expected=404, headers=foreign)
                await request("GET", path + "/events", expected=404, headers=foreign)
                await request("POST", path + "/cancel", expected=404, headers=foreign)
                await request(
                    "GET",
                    "http://127.0.0.1:8010/api/v1/asks/" + ask["local_run_id"],
                    expected=404,
                    headers=logins[1],
                )
                async with factory() as db:
                    receipts = list(
                        await db.scalars(
                            select(ToolCallReceipt).where(
                                ToolCallReceipt.tool_call_id.not_in(prior_receipts),
                                ToolCallReceipt.external_user_id
                                == state["accounts"][0]["user_id"],
                            )
                        )
                    )
                    item["tool_receipts"] = [
                        {
                            "call_id": row.tool_call_id,
                            "tool_name": row.tool_name,
                            "status": row.status,
                            "correct_user": row.external_user_id
                            == state["accounts"][0]["user_id"],
                        }
                        for row in receipts
                    ]
                assert (
                    item["status"] == "completed"
                    and item["final_text_exists"]
                    and item["app_answer_exists"]
                )
                if mode == "app_tools":
                    assert receipts and all(
                        row.status == "completed" for row in receipts
                    )
                    assert {row.tool_name for row in receipts} >= {
                        "personal_assistant.list_tasks",
                        "personal_assistant.search_notes",
                        "personal_assistant.list_calendar_events",
                    }
                item["cross_user_checks_passed"] = True
                report_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                )
                print(json.dumps(item, ensure_ascii=False), flush=True)
            # A newly created Run can be cancelled without starting a paid model call.
            pending = (
                await request(
                    "POST",
                    "http://127.0.0.1:8001/api/v1/app/runs",
                    expected=201,
                    headers=headers,
                    json={"user_request": "Cancellation verification only"},
                )
            ).json()
            cancelled = (
                await request(
                    "POST",
                    f"http://127.0.0.1:8001/api/v1/app/runs/{pending['run_id']}/cancel",
                    headers=headers,
                )
            ).json()
            assert cancelled["status"] == "cancelled"
            report["checks"] = [
                "event replay",
                "cross-user result/events/cancel denied",
                "App user isolation",
                "pending Run cancellation",
            ]
            report["status"] = "passed"
        finally:
            if report["status"] != "passed":
                report["status"] = "failed"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "configure", "serve", "verify"])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.mode == "prepare":
        asyncio.run(prepare())
    elif args.mode == "configure":
        configure(private_state())
    elif args.mode == "serve":
        import uvicorn
        from assistant_api.main import create_app

        uvicorn.run(
            create_app(settings=settings_for(private_state())),
            host="127.0.0.1",
            port=8010,
            access_log=False,
        )
    else:
        asyncio.run(verify(args.report))
