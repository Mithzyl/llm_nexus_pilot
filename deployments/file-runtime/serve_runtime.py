"""Serve fixed Office/PDF parsing over a permission-restricted local Unix socket.

Run as an administrator-managed executor on macOS Docker Desktop or Linux
Docker Engine. The API uses the socket, never the Docker daemon. Each accepted
request gets an offline, disposable Linux container. No host paths or commands
can be selected by clients. This is synchronous, not a durable task worker.
"""

import argparse
import json
import os
import selectors
import signal
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from manage_runtime import IMAGE_TAG, container_create_arguments

MAX_REQUEST_BYTES = ((20 * 1024 * 1024 + 2) // 3) * 4 + 4096
MAX_RESULT_BYTES = 4 * 1024 * 1024


def resolve_image(image_tag: str) -> str:
    """Resolve one administrator-selected Linux image before accepting any request."""

    import re

    image = json.loads(
        subprocess.run(
            ["docker", "image", "inspect", image_tag],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    )[0]
    if image.get("Os") != "linux" or not re.fullmatch(r"sha256:[a-f0-9]{64}", image["Id"]):
        raise ValueError("A locally built Linux runtime image is required")
    return image["Id"]


def parse_in_container(encoded: bytes, image_id: str) -> dict:
    """Run the fixed parser with bounded stdin/stdout and remove only its own container."""

    if len(encoded) > MAX_REQUEST_BYTES or not encoded.endswith(b"\n"):
        raise ValueError("Invalid request framing")
    name = f"nexuspilot-file-parse-{uuid.uuid4().hex}"
    arguments = container_create_arguments(name, image_id)
    arguments[-1:] = [
        "--interactive",
        "--log-driver",
        "none",
        "--entrypoint",
        "python",
        image_id,
        "-I",
        "/opt/file-runtime/parse_office.py",
    ]
    process = None
    try:
        subprocess.run(arguments, check=True, capture_output=True, timeout=30)
        with tempfile.TemporaryFile() as source:
            source.write(encoded)
            source.seek(0)
            process = subprocess.Popen(
                ["docker", "start", "--attach", "--interactive", name],
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            result = bytearray()
            deadline = time.monotonic() + 60
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError("File execution timed out")
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    result.extend(chunk)
                    if len(result) > MAX_RESULT_BYTES + 1:
                        raise ValueError("File output exceeds the supported size")
            exit_code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        report = json.loads(result)
        if exit_code:
            if report in ({"error": "office_parse_failed"}, {"error": "file_parse_failed"}):
                return report
            raise ValueError("Invalid failed parser output")
        if not isinstance(report, dict) or report.get("protocol") not in (
            "office_parse.v1", "file_parse.v1",
        ):
            raise ValueError("Invalid parser output")
        report["parser_version"] += "/" + image_id
        return report
    except Exception as error:
        print(
            json.dumps(
                {
                    "event": "file_execution_failed",
                    "container": name,
                    "error_type": type(error).__name__,
                }
            ),
            file=sys.stderr,
        )
        raise
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if process.stdout is not None:
                process.stdout.close()
        # Removing the uniquely named container also terminates a parser whose CLI timed out.
        try:
            subprocess.run(
                ["docker", "rm", "--force", name],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as error:
            print(
                json.dumps(
                    {
                        "event": "file_container_cleanup_failed",
                        "container": name,
                        "error_type": type(error).__name__,
                    }
                ),
                file=sys.stderr,
            )
            raise


class ParseHandler(socketserver.StreamRequestHandler):
    """Accept exactly one size-bounded request from an OS-authorized socket client."""

    timeout = 10

    def handle(self) -> None:
        """Answer a fixed availability probe or parse; never expose image IDs or host paths."""

        try:
            encoded = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if encoded == b'{"protocol":"file_capabilities.v1"}\n':
                report = {
                    "protocol": "file_capabilities.v1",
                    "formats": ["docx", "xlsx", "pptx", "pdf"],
                }
            else:
                report = parse_in_container(encoded, self.server.image_id)
        except Exception:
            report = {"error": "file_executor_failed"}
        try:
            self.wfile.write(json.dumps(report, ensure_ascii=False).encode() + b"\n")
        except OSError:
            # A disconnected observer does not prevent container cleanup above.
            pass


class FileRuntimeServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Bound executing requests before spawning threads; socket permissions authorize clients."""

    request_queue_size = 2
    daemon_threads = False

    def __init__(self, socket_path: str, image_id: str):
        """Bind only a new socket in an owner-controlled directory and pin the image ID."""

        parent = Path(socket_path).parent
        metadata = parent.stat()
        if (
            not Path(socket_path).is_absolute()
            or parent.is_symlink()
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o027
        ):
            raise ValueError("Socket directory must be owned by executor and mode 0700 or 0750")
        if os.path.lexists(socket_path):
            raise ValueError("Socket path already exists; do not replace a live or stale socket")
        self.image_id = image_id
        self.slots = threading.BoundedSemaphore(2)
        self.socket_identity = None
        super().__init__(socket_path, ParseHandler)
        self.socket_identity = Path(socket_path).stat().st_ino
        # A dedicated inherited directory group may read/write the socket, but not replace it.
        os.chown(socket_path, -1, metadata.st_gid)
        os.chmod(socket_path, 0o660 if metadata.st_mode & 0o050 else 0o600)

    def process_request(self, request, client_address) -> None:
        """Reject excess work immediately instead of accumulating unbounded waiting threads."""

        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b'{"error":"file_executor_busy"}\n')
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        """Release admission capacity after every request, including client disconnects."""

        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def server_close(self) -> None:
        """Wait for bounded active jobs and unlink only this server's unchanged socket."""

        super().server_close()
        path = Path(self.server_address)
        if (
            self.socket_identity is not None
            and path.exists()
            and path.lstat().st_ino == self.socket_identity
        ):
            path.unlink()


def main() -> int:
    """Start the executor with an explicit socket; optionally create its private local directory."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--image", default=IMAGE_TAG)
    parser.add_argument(
        "--create-directory",
        action="store_true",
        help="Create the socket's parent with mode 0700; existing permissions are validated",
    )
    arguments = parser.parse_args()

    def stop_executor(signum, frame):
        """Turn service termination into orderly waiting and cleanup of active containers."""

        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_executor)
    try:
        if arguments.create_directory:
            if not Path(arguments.socket).is_absolute():
                raise ValueError("Socket must be absolute")
            Path(arguments.socket).parent.mkdir(mode=0o700, exist_ok=True)
        with FileRuntimeServer(arguments.socket, resolve_image(arguments.image)) as server:
            print("File executor ready", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("File executor failed to start; check socket permissions and local Linux image")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
