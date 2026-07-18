"""Create, validate, and upload an independent PostgreSQL backup.

The web application's ``BackupArchive`` table is a convenience snapshot. This
tool is intended for a scheduled job with credentials to a separate,
versioned object-storage bucket.

Usage:
  python -m database.tools.offsite_backup
  python -m database.tools.offsite_backup --verify-key exis/postgres/example.dump
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from database.tools.cloud_db_download import pg_env_from_database_url


class BackupConfigurationError(ValueError):
    """Raised when the backup process cannot be configured safely."""


class BackupVerificationError(RuntimeError):
    """Raised when a dump or uploaded object cannot be verified."""


@dataclass(frozen=True)
class ObjectStorageConfig:
    bucket: str
    prefix: str = "exis/postgres"
    endpoint_url: str | None = None
    region: str | None = None
    server_side_encryption: str | None = None
    kms_key_id: str | None = None

    @classmethod
    def from_env(cls) -> "ObjectStorageConfig":
        bucket = (os.environ.get("EXIS_BACKUP_S3_BUCKET") or "").strip()
        if not bucket:
            raise BackupConfigurationError("EXIS_BACKUP_S3_BUCKET must be set")

        encryption = (os.environ.get("EXIS_BACKUP_S3_SSE") or "").strip() or None
        kms_key_id = (os.environ.get("EXIS_BACKUP_S3_KMS_KEY_ID") or "").strip() or None
        if kms_key_id and encryption != "aws:kms":
            raise BackupConfigurationError(
                "EXIS_BACKUP_S3_KMS_KEY_ID requires EXIS_BACKUP_S3_SSE=aws:kms"
            )

        return cls(
            bucket=bucket,
            prefix=(os.environ.get("EXIS_BACKUP_S3_PREFIX") or "exis/postgres").strip(),
            endpoint_url=(os.environ.get("EXIS_BACKUP_S3_ENDPOINT_URL") or "").strip() or None,
            region=(os.environ.get("EXIS_BACKUP_S3_REGION") or "").strip() or None,
            server_side_encryption=encryption,
            kms_key_id=kms_key_id,
        )


def build_object_key(prefix: str, now: datetime | None = None) -> str:
    timestamp = now or datetime.now(timezone.utc)
    normalized_prefix = prefix.strip().strip("/")
    key_prefix = f"{normalized_prefix}/" if normalized_prefix else ""
    return f"{key_prefix}{timestamp:%Y/%m/%d}/exis-postgres-{timestamp:%Y%m%dT%H%M%SZ}.dump"


def create_postgres_dump(
    database_url: str,
    output_path: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> int:
    """Write a custom-format dump and verify it without exposing the DB URL."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    environment = pg_env_from_database_url(database_url)
    runner(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(output_path),
        ],
        check=True,
        env=environment,
    )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise BackupVerificationError("pg_dump did not create a non-empty dump")

    runner(["pg_restore", "--list", str(output_path)], check=True, env=environment)
    return output_path.stat().st_size


def upload_dump(client: Any, config: ObjectStorageConfig, object_key: str, source_path: Path) -> int:
    """Upload a dump and confirm object storage reports the expected size."""
    expected_size = source_path.stat().st_size
    extra_args: dict[str, str] = {}
    if config.server_side_encryption:
        extra_args["ServerSideEncryption"] = config.server_side_encryption
    if config.kms_key_id:
        extra_args["SSEKMSKeyId"] = config.kms_key_id

    client.upload_file(str(source_path), config.bucket, object_key, ExtraArgs=extra_args)
    metadata = client.head_object(Bucket=config.bucket, Key=object_key)
    remote_size = metadata.get("ContentLength")
    if remote_size != expected_size:
        raise BackupVerificationError(
            f"uploaded object size mismatch (expected {expected_size}, got {remote_size})"
        )
    return expected_size


def verify_remote_object(client: Any, config: ObjectStorageConfig, object_key: str) -> int:
    """Confirm that a previously uploaded backup remains readable as metadata."""
    metadata = client.head_object(Bucket=config.bucket, Key=object_key)
    size = metadata.get("ContentLength")
    if not isinstance(size, int) or size <= 0:
        raise BackupVerificationError("remote backup is missing or empty")
    return size


def create_s3_client(config: ObjectStorageConfig) -> Any:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - exercised by deployment setup
        raise BackupConfigurationError(
            "boto3 is required; install requirements-tools.txt for this backup job"
        ) from exc

    kwargs: dict[str, str] = {}
    if config.endpoint_url:
        kwargs["endpoint_url"] = config.endpoint_url
    if config.region:
        kwargs["region_name"] = config.region
    return boto3.client("s3", **kwargs)


def run_backup(database_url: str, config: ObjectStorageConfig, *, client: Any | None = None) -> tuple[str, int]:
    """Create and verify one offsite database backup, returning its key and size."""
    storage_client = client or create_s3_client(config)
    object_key = build_object_key(config.prefix)
    with tempfile.TemporaryDirectory(prefix="exis-postgres-backup-") as temp_dir:
        dump_path = Path(temp_dir) / "database.dump"
        create_postgres_dump(database_url, dump_path)
        size = upload_dump(storage_client, config, object_key, dump_path)
    return object_key, size


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an offsite PostgreSQL backup.")
    parser.add_argument(
        "--verify-key",
        help="Verify a previously uploaded object key without creating a new dump.",
    )
    args = parser.parse_args()

    try:
        config = ObjectStorageConfig.from_env()
        client = create_s3_client(config)
        if args.verify_key:
            size = verify_remote_object(client, config, args.verify_key)
            print(f"[verified] s3://{config.bucket}/{args.verify_key} ({size} bytes)")
            return 0

        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise BackupConfigurationError("DATABASE_URL must be set")
        object_key, size = run_backup(database_url, config, client=client)
        print(f"[done] s3://{config.bucket}/{object_key} ({size} bytes)")
        return 0
    except BackupConfigurationError as exc:
        print(f"[error] backup configuration: {exc}")
    except (BackupVerificationError, FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"[error] backup verification failed: {exc}")
    except Exception as exc:  # Avoid rendering provider exceptions that may contain request details.
        print(f"[error] offsite backup failed ({type(exc).__name__})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
