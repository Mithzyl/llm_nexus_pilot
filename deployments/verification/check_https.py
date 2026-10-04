"""Exercise the local-only production stack before and after container recreation.

Run with the private verification directory and stage `before` or `after`.
Only https://chat.localhost:19443 routed to 127.0.0.1 is reachable by this script.
Uses synthetic accounts and the private fixture Provider, never real model keys.
Cookie jars and state stay in the caller-owned mode-0700 temporary directory.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ORIGIN = "https://chat.localhost:19443"
PASSWORD = "local-preverify-only-password"
FILE_TEXT = b"FILE_ONLY_PREVERIFY_2718"


class Client:
    """Send verified HTTPS requests with isolated browser-like cookie jars."""

    def __init__(self, directory, actor):
        """Keep each synthetic user's cookies private and trust only the lab certificate."""
        self.directory = directory
        self.jar = directory / f"{actor}.cookies"
        self.header_file = directory / f"{actor}.headers"

    def cookies(self):
        """Read only Netscape-format cookie values needed for CSRF and test replay."""
        values = {}
        if self.jar.exists():
            for line in self.jar.read_text().splitlines():
                fields = line.split("\t")
                if len(fields) == 7:
                    values[fields[5]] = fields[6]
        return values

    def request(
        self,
        method,
        path,
        *,
        payload=None,
        content=None,
        stream=False,
        csrf=True,
        origin=ORIGIN,
    ):
        """Capture status, data and stream arrival times without printing secrets or bodies."""
        args = [
            "curl",
            "--silent",
            "--show-error",
            "--no-buffer",
            "--max-time",
            "60",
            "--resolve",
            "chat.localhost:19443:127.0.0.1",
            "--cacert",
            str(self.directory / "tls/fullchain.pem"),
            "--cookie",
            str(self.jar),
            "--cookie-jar",
            str(self.jar),
            "--dump-header",
            str(self.header_file),
            "--request",
            method,
            "--header",
            f"Origin: {origin}",
            "--write-out",
            "\nHTTP_STATUS:%{http_code}\n",
        ]
        if csrf and (token := self.cookies().get("nexuspilot_csrf")):
            args += ["--header", f"X-CSRF-Token: {token}"]
        if payload is not None or content is not None:
            args += [
                "--header",
                "Content-Type: "
                + (
                    "application/json"
                    if payload is not None
                    else "application/octet-stream"
                ),
                "--data-binary",
                "@-",
            ]
        process = subprocess.Popen(
            [*args, ORIGIN + path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        process.stdin.write(
            json.dumps(payload)
            if payload is not None
            else (content.decode() if content is not None else "")
        )
        process.stdin.close()
        lines, delta_times, packets = [], [], []
        for line in process.stdout:
            lines.append(line)
            if stream and line.startswith("data: "):
                packet = json.loads(line[6:])
                packets.append(packet)
                if packet.get("type") == "response.text.delta":
                    delta_times.append(time.monotonic())
        process.stderr.read()
        process.wait()
        if process.returncode:
            raise RuntimeError(f"HTTPS transport failed with exit {process.returncode}")
        body, code = "".join(lines).rsplit("\nHTTP_STATUS:", 1)
        return int(code.strip()), body, packets, delta_times

    def json(self, method, path, expected, payload=None, **kwargs):
        """Require the exact API status and decode JSON only when there is a body."""
        status, body, _, _ = self.request(method, path, payload=payload, **kwargs)
        assert status == expected, (method, path, status, expected)
        return json.loads(body) if body else None


def response_payload(turn):
    """Use persisted turn IDs and omit file bytes so only backend context assembly can supply them."""
    return {
        "run_id": turn["run"]["run_id"],
        "current_user_message_id": turn["message"]["message_id"],
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "input": turn["message"]["content_text"],
        "idempotency_key": uuid.uuid4().hex,
    }


def save_assistant(client, session_id, turn, result):
    """Follow the Web caller's explicit message-save step after a completed model response."""
    return client.json(
        "POST",
        f"/api/nexus/sessions/{session_id}/messages",
        201,
        {
            "role": "assistant",
            "content_text": result["output_text"],
            "run_id": turn["run"]["run_id"],
            "source_model_attempt_id": result["id"],
        },
    )


def before(directory):
    """Validate ingress, accounts, file inputs and delayed SSE before a cold recreation."""
    a, b, anonymous = (
        Client(directory, "a"),
        Client(directory, "b"),
        Client(directory, "anonymous"),
    )
    assert anonymous.request("GET", "/")[0] == 200
    assert anonymous.request("GET", "/api/nexus/sessions")[0] == 401
    assert anonymous.request("GET", "/api/v1/sessions")[0] == 404
    for client, actor in ((a, "preverify-a"), (b, "preverify-b")):
        client.json(
            "POST",
            "/api/nexus/auth/login",
            200,
            {"email": f"{actor}@example.test", "password": PASSWORD},
        )
        cookies = [
            line.lower()
            for line in client.header_file.read_text().splitlines()
            if line.lower().startswith("set-cookie: nexuspilot_session=")
        ]
        assert cookies and all(
            x in cookies[0] for x in ("secure", "httponly", "samesite=lax")
        )
    a.json("POST", "/api/nexus/sessions", 403, {"title": "must-not-exist"}, csrf=False)
    a.json(
        "POST",
        "/api/nexus/sessions",
        403,
        {"title": "must-not-exist"},
        origin="https://foreign.invalid",
    )
    session = a.json(
        "POST", "/api/nexus/sessions", 201, {"title": "PRIVATE_PREVERIFY_A"}
    )
    sid = session["session_id"]
    assert b.request("GET", f"/api/nexus/sessions/{sid}")[0] == 404
    assert "PRIVATE_PREVERIFY_A" not in b.request("GET", "/api/nexus/sessions")[1]
    file = a.json(
        "POST",
        "/api/nexus/files",
        201,
        {
            "filename": "fixture.txt",
            "size_bytes": len(FILE_TEXT),
            "content_hash": hashlib.sha256(FILE_TEXT).hexdigest(),
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    fid = file["file_id"]
    uploaded = a.json("PUT", f"/api/nexus/files/{fid}/content", 200, content=FILE_TEXT)
    assert uploaded["status"] == "ready"
    assert a.request("GET", f"/api/nexus/files/{fid}/content")[1].encode() == FILE_TEXT
    assert b.request("GET", f"/api/nexus/files/{fid}/content")[0] == 404
    first = a.json(
        "POST",
        f"/api/nexus/sessions/{sid}/turns",
        201,
        {
            "content_text": "Read the attachment",
            "file_ids": [fid],
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    code, _, events, arrivals = a.request(
        "POST",
        "/api/nexus/responses",
        payload={**response_payload(first), "stream": True},
        stream=True,
    )
    assert code == 200
    assert any(event["type"] == "response.completed" for event in events)
    assert not any(event["type"] == "response.failed" for event in events)
    answer = "".join(
        event["data"]["delta"]
        for event in events
        if event["type"] == "response.text.delta"
    )
    assert answer == "FILE_CONTEXT_OK", answer
    assert len(arrivals) >= 2 and arrivals[-1] - arrivals[0] > 0.8
    completed = next(
        event["data"]["response"]
        for event in events
        if event["type"] == "response.completed"
    )
    save_assistant(a, sid, first, completed)
    second = a.json(
        "POST",
        f"/api/nexus/sessions/{sid}/turns",
        201,
        {
            "content_text": "Continue about that attachment",
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    result = a.json("POST", "/api/nexus/responses", 200, response_payload(second))
    assert result["output_text"] == "FILE_CONTEXT_OK", result.get("output_text")
    save_assistant(a, sid, second, result)
    messages = a.json(
        "GET", f"/api/nexus/sessions/{sid}/messages/latest?limit=100", 200
    )["items"]
    assert len(messages) == 4
    (directory / "state.json").write_text(
        json.dumps(
            {
                "session_id": sid,
                "file_id": fid,
                "messages": [item["message_id"] for item in messages],
            }
        )
    )
    return {
        "https_identity_and_file": "passed",
        "file_followup": "passed",
        "sse_unbuffered": "passed",
        "delta_span_seconds": round(arrivals[-1] - arrivals[0], 3),
    }


def after(directory):
    """Use the same cookies after container recreation and verify persisted context and revocation."""
    a, b = Client(directory, "a"), Client(directory, "b")
    state = json.loads((directory / "state.json").read_text())
    sid, fid = state["session_id"], state["file_id"]
    messages = a.json(
        "GET", f"/api/nexus/sessions/{sid}/messages/latest?limit=100", 200
    )["items"]
    assert [item["message_id"] for item in messages] == state["messages"]
    assert a.request("GET", f"/api/nexus/files/{fid}/content")[1].encode() == FILE_TEXT
    assert b.request("GET", f"/api/nexus/sessions/{sid}")[0] == 404
    third = a.json(
        "POST",
        f"/api/nexus/sessions/{sid}/turns",
        201,
        {
            "content_text": "Read the earlier file after restart",
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    result = a.json("POST", "/api/nexus/responses", 200, response_payload(third))
    assert result["output_text"] == "FILE_CONTEXT_OK"
    save_assistant(a, sid, third, result)
    a.json("DELETE", f"/api/nexus/files/{fid}", 204)
    assert a.request("GET", f"/api/nexus/files/{fid}/content")[0] == 404
    original_cookie_jar = a.jar.read_text()
    a.json("POST", "/api/nexus/auth/logout", 204)
    a.jar.write_text(original_cookie_jar)
    assert a.request("GET", f"/api/nexus/sessions/{sid}")[0] == 401
    return {
        "cold_recreation_persistence": "passed",
        "file_context_after_recreation": "passed",
        "delete_and_revoked_cookie": "passed",
    }


if __name__ == "__main__":
    os.umask(0o077)
    directory = Path(sys.argv[1])
    result = {"before": before, "after": after}[sys.argv[2]](directory)
    (directory / f"{sys.argv[2]}-result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
