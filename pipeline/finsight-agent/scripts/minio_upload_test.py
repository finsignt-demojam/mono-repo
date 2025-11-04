#!/usr/bin/env python3
"""Upload a local file to the MinIO `audio-inbox` bucket to exercise the FinSight
eventing pipeline end-to-end.

The script talks to MinIO via the S3 API (boto3) so the upload triggers the
KafkaSource → Broker → Trigger chain that ultimately invokes the Kubeflow
pipeline.  Defaults are read from environment variables when available so it is
easy to tweak credentials or endpoints without editing the file.

Usage example:

    python scripts/minio_upload_test.py path/to/earnings-call.mp3 \
        --object-key demo/call.mp3

If you need to override the defaults, set any of the following environment
variables or pass the matching CLI flags:

    MINIO_ENDPOINT   (default: https://minio-api-minio.apps.rosa-58cx6.acrs.p3.openshiftapps.com)
    MINIO_BUCKET     (default: audio-inbox)
    MINIO_ACCESS_KEY (default: minioadmin)
    MINIO_SECRET_KEY (default: minioadmin123)
    MINIO_PREFIX     (optional object-key prefix)

Requires boto3:  `pip install boto3`
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
import uuid
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


DEFAULT_ENDPOINT = "https://minio-api-minio.apps.rosa.rosa-58cx6.acrs.p3.openshiftapps.com"
DEFAULT_BUCKET = "audio-inbox"
DEFAULT_ACCESS_KEY = "minioadmin"
DEFAULT_SECRET_KEY = "minioadmin123"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a file to MinIO to trigger the FinSight event pipeline."
    )
    parser.add_argument(
        "file",
        type=Path,
        help="Path to the local file to upload (e.g. an MP3 earnings call recording).",
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("MINIO_BUCKET", DEFAULT_BUCKET),
        help="Target bucket (default: %(default)s).",
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("MINIO_ENDPOINT", DEFAULT_ENDPOINT),
        help="MinIO S3 endpoint URL (default: %(default)s).",
    )
    parser.add_argument(
        "--access-key",
        default=os.getenv("MINIO_ACCESS_KEY", DEFAULT_ACCESS_KEY),
        help="MinIO access key (default: value from MINIO_ACCESS_KEY or %(default)s).",
    )
    parser.add_argument(
        "--secret-key",
        default=os.getenv("MINIO_SECRET_KEY", DEFAULT_SECRET_KEY),
        help="MinIO secret key (default: value from MINIO_SECRET_KEY or %(default)s).",
    )
    parser.add_argument(
        "--object-key",
        default=None,
        help="Optional object key to use inside the bucket.  If omitted, a UUID-based key is generated.",
    )
    parser.add_argument(
        "--prefix",
        default=os.getenv("MINIO_PREFIX", ""),
        help="Optional key prefix (applied only when --object-key is not provided).",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Disable TLS certificate verification (useful for self-signed clusters).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be uploaded without performing the upload.",
    )
    return parser.parse_args()


def build_object_key(args: argparse.Namespace, file_path: Path) -> str:
    if args.object_key:
        return args.object_key

    suffix = file_path.suffix or ""
    prefix = args.prefix.rstrip("/")
    random_name = f"{uuid.uuid4()}" + suffix
    return f"{prefix}/{random_name}" if prefix else random_name


def main() -> int:
    args = parse_args()

    if not args.file.exists():
        print(f"[error] File not found: {args.file}", file=sys.stderr)
        return 1

    object_key = build_object_key(args, args.file)
    content_type = mimetypes.guess_type(args.file.name)[0] or "application/octet-stream"

    print("Preparing upload:")
    print(f"  Endpoint : {args.endpoint}")
    print(f"  Bucket   : {args.bucket}")
    print(f"  Object   : {object_key}")
    print(f"  File     : {args.file} ({content_type})")

    if args.dry_run:
        print("[dry-run] Skipping upload.")
        return 0

    session = boto3.session.Session(
        aws_access_key_id=args.access_key,
        aws_secret_access_key=args.secret_key,
    )

    client = session.client(
        "s3",
        endpoint_url=args.endpoint,
        config=Config(signature_version="s3v4"),
        verify=not args.no_verify,
    )

    try:
        client.upload_file(
            Filename=str(args.file),
            Bucket=args.bucket,
            Key=object_key,
            ExtraArgs={"ContentType": content_type},
        )
        head = client.head_object(Bucket=args.bucket, Key=object_key)
    except ClientError as exc:
        print(f"[error] Upload failed: {exc}", file=sys.stderr)
        return 1

    etag = head.get("ETag", "?")
    size = head.get("ContentLength", "?")
    print("Upload complete:")
    print(f"  ETag  : {etag}")
    print(f"  Size  : {size} bytes")
    print(
        "The MinIO Kafka notification should now fire, triggering the Knative Broker and"
        " downstream Kubeflow pipeline. Monitor the pipeline UI or Kafka topics to"
        " confirm end-to-end processing."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())





