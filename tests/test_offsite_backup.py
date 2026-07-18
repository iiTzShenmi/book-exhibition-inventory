from pathlib import Path

import pytest

from database.tools import offsite_backup


class FakeStorageClient:
    def __init__(self, remote_size: int | None = None):
        self.remote_size = remote_size
        self.uploads: list[tuple[str, str, str, dict[str, str]]] = []

    def upload_file(self, filename, bucket, key, ExtraArgs):
        self.uploads.append((filename, bucket, key, ExtraArgs))
        if self.remote_size is None:
            self.remote_size = Path(filename).stat().st_size

    def head_object(self, *, Bucket, Key):
        return {"ContentLength": self.remote_size}


def test_offsite_backup_requires_bucket(monkeypatch):
    monkeypatch.delenv("EXIS_BACKUP_S3_BUCKET", raising=False)

    with pytest.raises(offsite_backup.BackupConfigurationError):
        offsite_backup.ObjectStorageConfig.from_env()


def test_postgres_dump_uses_libpq_environment_and_validates_dump(tmp_path):
    commands = []
    output_path = tmp_path / "database.dump"
    database_url = "postgresql://backup:secret@example.com:5432/exis?sslmode=require"

    def runner(command, *, check, env):
        commands.append((command, check, env))
        if command[0] == "pg_dump":
            Path(command[command.index("--file") + 1]).write_bytes(b"verified dump")

    size = offsite_backup.create_postgres_dump(database_url, output_path, runner=runner)

    assert size == len(b"verified dump")
    assert [command[0][0] for command in commands] == ["pg_dump", "pg_restore"]
    assert all(database_url not in " ".join(command[0]) for command in commands)
    assert commands[0][2]["PGPASSWORD"] == "secret"
    assert commands[0][2]["PGDATABASE"] == "exis"


def test_uploaded_dump_is_checked_against_remote_object_size(tmp_path):
    dump_path = tmp_path / "database.dump"
    dump_path.write_bytes(b"backup bytes")
    client = FakeStorageClient()
    config = offsite_backup.ObjectStorageConfig(
        bucket="independent-backups",
        server_side_encryption="AES256",
    )

    size = offsite_backup.upload_dump(client, config, "exis/postgres/test.dump", dump_path)

    assert size == len(b"backup bytes")
    assert client.uploads == [
        (
            str(dump_path),
            "independent-backups",
            "exis/postgres/test.dump",
            {"ServerSideEncryption": "AES256"},
        )
    ]


def test_uploaded_dump_size_mismatch_fails_verification(tmp_path):
    dump_path = tmp_path / "database.dump"
    dump_path.write_bytes(b"backup bytes")
    client = FakeStorageClient(remote_size=1)
    config = offsite_backup.ObjectStorageConfig(bucket="independent-backups")

    with pytest.raises(offsite_backup.BackupVerificationError):
        offsite_backup.upload_dump(client, config, "exis/postgres/test.dump", dump_path)


def test_remote_backup_verification_rejects_empty_objects():
    client = FakeStorageClient(remote_size=0)
    config = offsite_backup.ObjectStorageConfig(bucket="independent-backups")

    with pytest.raises(offsite_backup.BackupVerificationError):
        offsite_backup.verify_remote_object(client, config, "exis/postgres/test.dump")
