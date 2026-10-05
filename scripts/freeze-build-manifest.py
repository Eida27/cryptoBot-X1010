"""Freeze locked dependency identities into the distributable package."""

import hashlib
import json
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
lock_bytes = (root / "uv.lock").read_bytes()
lock = tomllib.loads(lock_bytes.decode())
project = tomllib.loads((root / "pyproject.toml").read_text())
manifest = {
    "format": 1,
    "lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
    "roots": project["project"]["dependencies"],
    "versions": {item["name"]: item["version"] for item in lock["package"]},
}
(root / "src/crypto_bot/_build_manifest.json").write_text(
    json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
)
