#!/bin/sh
# Check local MinIO readiness with the bundled mc client; this image has no curl.
# Invoked by Docker healthcheck. Credentials remain in the process environment,
# not command arguments, and diagnostic output is suppressed to avoid leaking them.
set -eu
export MC_HOST_local="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@127.0.0.1:9000"
exec timeout 4s mc ready local >/dev/null 2>&1
