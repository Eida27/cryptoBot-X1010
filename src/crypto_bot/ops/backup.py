import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from crypto_bot.storage.database import ProcessLock


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


@dataclass(frozen=True)
class BackupManifest:
    path: Path
    checksum: str
    created_ms: int

    @classmethod
    def read(cls, path: Path) -> "BackupManifest":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(Path(data["path"]), data["checksum"], data["created_ms"])


@dataclass(frozen=True)
class RestoreReport:
    path: Path
    integrity: str
    startup_state: str = "PAUSED_RECONCILIATION_REQUIRED"


def check_disk(path: Path, required: int = 64 * 1024 * 1024) -> None:
    if shutil.disk_usage(path).free < required:
        raise OSError("Insufficient free disk for durable writes")


def create_backup(database: Path, destination: Path) -> BackupManifest:
    if not database.is_file():
        raise ValueError("Database does not exist")
    destination.mkdir(parents=True, exist_ok=True)
    check_disk(destination, max(database.stat().st_size * 2, 64 * 1024 * 1024))
    now = datetime.now(UTC)
    target = destination / f"{database.stem}-{now:%Y-%m-%d}.sqlite3"
    temporary = target.with_suffix(".tmp")
    if temporary.is_symlink() or target.is_symlink():
        raise ValueError("Backup paths must not be symlinks")
    source = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    backup = sqlite3.connect(temporary)
    try:
        source.backup(backup)
        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup integrity check failed")
    finally:
        source.close()
        backup.close()
    os.replace(temporary, target)
    target.chmod(0o600)
    manifest = BackupManifest(target.resolve(), file_hash(target), int(now.timestamp() * 1000))
    target.with_suffix(".json").write_text(json.dumps({"path": str(manifest.path), "checksum": manifest.checksum, "created_ms": manifest.created_ms}), encoding="utf-8")
    retained = sorted(destination.glob(f"{database.stem}-????-??-??.sqlite3"), reverse=True)
    for old in retained[7:]:
        if old.is_file() and not old.is_symlink():
            old.unlink()
            old.with_suffix(".json").unlink(missing_ok=True)
    return manifest


def verify_restore(manifest: BackupManifest, destination: Path) -> RestoreReport:
    if destination.exists() or destination.with_suffix(destination.suffix + "-wal").exists():
        raise ValueError("Restore destination already exists; use an isolated path")
    if file_hash(manifest.path) != manifest.checksum:
        raise ValueError("Backup checksum differs")
    destination.parent.mkdir(parents=True, exist_ok=True)
    check_disk(destination.parent)
    with ProcessLock(destination):
        source = sqlite3.connect(f"{manifest.path.resolve().as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(destination)
        try:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity check failed")
            source.backup(target)
            target.execute("UPDATE run_state SET reconciled=0,state=CASE WHEN halt_reason IS NULL THEN 'PAUSED' ELSE 'HALTED' END")
            target.execute("UPDATE runs SET qualification_status='INTERRUPTED' WHERE active=1 AND mode='PAPER'")
            target.execute("UPDATE web_sessions SET revoked=1")
            target.commit()
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError("Restored integrity check failed")
        finally:
            source.close()
            target.close()
    destination.chmod(0o600)
    return RestoreReport(destination.resolve(), integrity)
