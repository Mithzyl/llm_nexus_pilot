"""Local JSON-RPC fixture for serve_identity_memory_fixture.py --mcp; no external requests."""

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCHEMA = {"type":"object", "properties":{}, "additionalProperties":False}


def configure_mcp():
    """Configure one loopback-only synthetic service for one explicitly seeded test owner."""
    token = secrets.token_urlsafe(32)
    os.environ["NEXUSPILOT_MCP_CONNECTIONS_JSON"] = json.dumps({"Local MCP": {
        "url":"http://127.0.0.1:18014/mcp", "token":token, "user_ids":["mcp-fixture-user"],
        "allow_local_http":True, "tools":{"lookup":{"description":"查询本地测试条目", "input_schema":SCHEMA}}
    }})
    return token


async def seed_user(directory, *, user_id="mcp-fixture-user", email="mcp-fixture@example.test",
                    display_name="MCP Fixture", filename="mcp-login.json"):
    """Provision only the fixture login and keep its generated password in a private file."""
    from nexuspilot_api.infrastructure.database import database_session_factory, engine
    from nexuspilot_api.features.identity.services.authentication_service import provision_local_password_user
    password = secrets.token_urlsafe(32)
    async with database_session_factory() as db:
        await provision_local_password_user(db, user_id=user_id, display_name=display_name,
            email=email, password=password)
    fd = os.open(directory / filename, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump({"email":email, "password":password}, output)
    await engine.dispose()


def start_server(token):
    """Serve the minimal real HTTP protocol used by discovery; suppress request diagnostics."""
    class Handler(BaseHTTPRequestHandler):
        """Accept only the local fixture credential and a bounded JSON-RPC body."""

        def log_message(self, *_):
            """Do not log private HTTP headers or request payloads."""

        def do_POST(self):
            """Return initialization/discovery responses; reject all business execution."""
            self.connection.settimeout(5)
            if self.headers.get("Authorization") != "Bearer " + token:
                self.send_error(401); return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                self.send_error(400); return
            body = json.loads(self.rfile.read(size))
            if body["method"] == "notifications/initialized":
                self.send_response(202); self.end_headers(); return
            if body["method"] == "initialize":
                result = {"protocolVersion":"2025-03-26", "capabilities":{"tools":{}}}
            elif body["method"] == "tools/list":
                result = {"tools":[{"name":"lookup", "inputSchema":SCHEMA}]}
            else:
                self.send_error(400); return
            content = json.dumps({"jsonrpc":"2.0", "id":body["id"], "result":result}).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1",18014), Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    return server, thread
