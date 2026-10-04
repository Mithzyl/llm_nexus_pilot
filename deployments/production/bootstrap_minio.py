"""Explicit first-deployment MinIO initialization using credentials supplied by Compose.

Run the production `storage-init` service after MinIO is healthy. Creates the private
bucket and a dedicated app user with only object access to that bucket. Re-running
applies the same configured app credentials/policy; it never deletes user objects.
"""

import json
import os
import tempfile

from minio import Minio, MinioAdmin
from minio.credentials import StaticProvider


def main():
    """Initialize bucket-scoped access without printing credentials or server responses."""
    endpoint = os.environ["MINIO_ENDPOINT"]
    root = os.environ["MINIO_ROOT_USER"]
    secret = os.environ["MINIO_ROOT_PASSWORD"]
    bucket = os.environ["MINIO_APP_BUCKET"]
    user = os.environ["MINIO_APP_USER"]
    client = Minio(endpoint, access_key=root, secret_key=secret, secure=False)
    admin = MinioAdmin(endpoint, credentials=StaticProvider(root, secret), secure=False)
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetBucketLocation",
                    "s3:ListBucket",
                    "s3:ListBucketMultipartUploads",
                ],
                "Resource": [f"arn:aws:s3:::{bucket}"],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    "s3:AbortMultipartUpload",
                    "s3:ListMultipartUploadParts",
                ],
                "Resource": [f"arn:aws:s3:::{bucket}/*"],
            },
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as output:
        json.dump(policy, output)
        output.flush()
        admin.policy_add("nexuspilot-files", output.name)
    admin.user_add(user, os.environ["MINIO_APP_PASSWORD"])
    admin.policy_set("nexuspilot-files", user=user)
    print("Private bucket and scoped application user initialized.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Storage initialization failed; inspect connectivity and private configuration."
        ) from None
