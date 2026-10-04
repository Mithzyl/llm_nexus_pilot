"""Private HTTP model fixture for local deployment checks, never production deployment.

Implements only the chat-completions shape used by the DeepSeek codec. No external
network requests, credentials or model inference. A delayed second chunk detects
accidental buffering across the real API/BFF/Nginx connection.
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FILE_MARKER = "FILE_ONLY_PREVERIFY_2718"


class Handler(BaseHTTPRequestHandler):
    """Serve deterministic synthetic responses with bounded request bytes."""

    def log_message(self, *_args):
        """Avoid retaining request headers, messages or synthetic credentials."""

    def do_GET(self):
        """Provide a simple readiness endpoint for the local fixture container."""
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        """Report whether the actual gateway request contains extracted file text."""
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length < 2_000_000 or not self.path.endswith("/chat/completions"):
            self.send_error(400)
            return
        payload = json.loads(self.rfile.read(length))
        has_file = FILE_MARKER in json.dumps(payload.get("messages", []))
        output = "FILE_CONTEXT_OK" if has_file else "NO_FILE_CONTEXT"
        usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        if not payload.get("stream"):
            body = json.dumps(
                {
                    "id": "fixture-completion",
                    "model": payload["model"],
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": output},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for part in (output[:7], output[7:]):
            chunk = {
                "id": "fixture-completion",
                "model": payload["model"],
                "choices": [
                    {"index": 0, "delta": {"content": part}, "finish_reason": None}
                ],
            }
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.flush()
            time.sleep(1.25)
        final = {
            "id": "fixture-completion",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": usage,
        }
        self.wfile.write(
            ("data: " + json.dumps(final) + "\n\ndata: [DONE]\n\n").encode()
        )
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
