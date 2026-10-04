"""Verify an explicitly authorized HTTPS deployment using dedicated test identities.

Run with --origin, --directory and --model. The private directory must contain
fullchain.pem and deploy-check-{a,b}-login.json (email/password). This creates
synthetic sessions and one text upload and makes two short real model calls.
It never reads production user data. Reports omit credentials; browser state
and test resource IDs stay in the mode-0700 directory. Disable the dedicated
test identities after browser verification. Requires the API's httpx dependency.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import ssl
import time
import uuid

import httpx


def require(client, method, path, expected, *, csrf=True, **kwargs):
    """Check exact HTTP outcomes and add the authenticated browser's CSRF header."""
    headers = kwargs.pop("headers", {})
    if csrf and (token := client.cookies.get("nexuspilot_csrf")):
        headers["X-CSRF-Token"] = token
    response = client.request(method, path, headers=headers, **kwargs)
    assert response.status_code == expected, (
        method,
        path,
        response.status_code,
        expected,
    )
    return response


def main():
    """Check HTTPS auth, ownership, text storage, streaming, context persistence and revocation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    assert args.origin.startswith("https://")
    assert args.directory.stat().st_mode & 0o077 == 0
    os.umask(0o077)
    clients = [
        httpx.Client(
            base_url=args.origin,
            timeout=90,
            verify=ssl.create_default_context(cafile=args.directory / "fullchain.pem"),
            headers={"Origin": args.origin},
        )
        for _ in range(3)
    ]
    a, b, anonymous = clients
    report = {}
    try:
        require(anonymous, "GET", "/", 200)
        require(anonymous, "GET", "/api/nexus/sessions", 401)
        for path in ("/api/v1/sessions", "/docs", "/openapi.json"):
            require(anonymous, "GET", path, 404)
        for client, actor in ((a, "a"), (b, "b")):
            credential = json.loads(
                (args.directory / f"deploy-check-{actor}-login.json").read_text()
            )
            response = require(
                client,
                "POST",
                "/api/nexus/auth/login",
                200,
                json={key: credential[key] for key in ("email", "password")},
            )
            cookie = next(
                x.lower()
                for x in response.headers.get_list("set-cookie")
                if x.startswith("nexuspilot_session=")
            )
            assert all(
                flag in cookie for flag in ("httponly", "secure", "samesite=lax")
            )
        require(
            a,
            "POST",
            "/api/nexus/sessions",
            403,
            csrf=False,
            json={"title": "must-not-exist"},
        )
        require(
            a,
            "POST",
            "/api/nexus/sessions",
            403,
            headers={"Origin": "https://foreign.invalid"},
            json={"title": "must-not-exist"},
        )
        report["https_login_secure_cookies_csrf_anonymous_denial"] = "passed"
        models = require(
            a, "GET", "/api/nexus/models?requires_streaming=true", 200
        ).json()
        report["model_directory"] = [
            {
                k: model[k]
                for k in (
                    "model_identifier",
                    "registry_available",
                    "selectable",
                    "unavailable_reason",
                )
            }
            for model in models
        ]
        assert any(
            model["model_identifier"] == args.model and model["selectable"]
            for model in models
        )
        sid = require(
            a,
            "POST",
            "/api/nexus/sessions",
            201,
            json={"title": "Deployment verification (synthetic)"},
        ).json()["session_id"]
        require(b, "GET", f"/api/nexus/sessions/{sid}", 404)
        assert sid not in require(b, "GET", "/api/nexus/sessions", 200).text
        marker = "DEPLOY_FILE_" + uuid.uuid4().hex[:12]
        content = ("校验码：" + marker).encode()
        fid = require(
            a,
            "POST",
            "/api/nexus/files",
            201,
            json={
                "filename": "deployment-check.txt",
                "size_bytes": len(content),
                "content_hash": hashlib.sha256(content).hexdigest(),
                "idempotency_key": uuid.uuid4().hex,
            },
        ).json()["file_id"]
        uploaded = require(
            a,
            "PUT",
            f"/api/nexus/files/{fid}/content",
            200,
            headers={"Content-Type": "application/octet-stream"},
            content=content,
        ).json()
        assert uploaded["status"] == "ready"
        assert (
            require(a, "GET", f"/api/nexus/files/{fid}/content", 200).content == content
        )
        require(b, "GET", f"/api/nexus/files/{fid}/content", 404)
        require(b, "DELETE", f"/api/nexus/files/{fid}", 404)
        report["cross_user_sessions_files_and_text_upload"] = "passed"
        answers = []
        for index in range(2):
            turn = require(
                a,
                "POST",
                f"/api/nexus/sessions/{sid}/turns",
                201,
                json={
                    "content_text": "只回复附件中的完整校验码，不要增加其他文字。"
                    if index == 0
                    else "请再重复一次之前附件中的完整校验码，不要增加其他文字。",
                    "idempotency_key": uuid.uuid4().hex,
                    **({"file_ids": [fid]} if index == 0 else {}),
                },
            ).json()
            payload = {
                "run_id": turn["run"]["run_id"],
                "current_user_message_id": turn["message"]["message_id"],
                "provider": "deepseek",
                "model": args.model,
                "input": turn["message"]["content_text"],
                "stream": True,
                "reasoning": {"enabled": False},
                "max_output_tokens": 64,
                "idempotency_key": uuid.uuid4().hex,
            }
            events, arrivals = [], []
            with a.stream(
                "POST",
                "/api/nexus/responses",
                json=payload,
                headers={"X-CSRF-Token": a.cookies.get("nexuspilot_csrf")},
            ) as response:
                assert response.status_code == 200, response.status_code
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        events.append(event)
                        if event.get("type") == "response.text.delta":
                            arrivals.append(time.monotonic())
            failed = [event for event in events if event["type"] == "response.failed"]
            assert not failed, failed
            completed = next(
                event["data"]["response"]
                for event in events
                if event["type"] == "response.completed"
            )
            assert marker in completed["output_text"], (
                "Model did not read the file marker"
            )
            answers.append(completed["output_text"])
            require(
                a,
                "POST",
                f"/api/nexus/sessions/{sid}/messages",
                201,
                json={
                    "role": "assistant",
                    "content_text": completed["output_text"],
                    "run_id": turn["run"]["run_id"],
                    "source_model_attempt_id": completed["id"],
                },
            )
            report[f"real_model_turn_{index + 1}"] = {
                "status": "passed",
                "deltas": len(arrivals),
                "delta_span_seconds": round(arrivals[-1] - arrivals[0], 3)
                if arrivals
                else None,
            }
        messages = require(
            a, "GET", f"/api/nexus/sessions/{sid}/messages/latest?limit=100", 200
        ).json()["items"]
        assert len(messages) == 4
        report["saved_file_context_followup"] = "passed"
        old_cookies = dict(b.cookies)
        require(b, "POST", "/api/nexus/auth/logout", 204)
        b.cookies.update(old_cookies)
        require(b, "GET", "/api/nexus/sessions", 401)
        report["logout_cookie_replay_denied"] = "passed"
        (args.directory / "release-state.json").write_text(
            json.dumps({"session_id": sid, "file_id": fid, "answers": answers})
        )
        (args.directory / "release-browser-state.json").write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": cookie.name,
                            "value": cookie.value,
                            "domain": cookie.domain,
                            "path": cookie.path,
                            "expires": cookie.expires or -1,
                            "secure": True,
                            "httpOnly": cookie.name == "nexuspilot_session",
                            "sameSite": "Lax",
                        }
                        for cookie in a.cookies.jar
                    ],
                    "origins": [],
                }
            )
        )
    finally:
        for client in clients:
            client.close()
        (args.directory / "release-result.json").write_text(
            json.dumps(report, indent=2)
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
