from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Iterable


class ImmutableArtifactError(RuntimeError):
    pass


class ArtifactIntegrityError(ValueError):
    pass


def json_bytes(value: object, *, pretty: bool = False) -> bytes:
    if is_dataclass(value):
        value = asdict(value)
    if pretty:
        return (json.dumps(value, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def jsonl_bytes(rows: Iterable[object]) -> bytes:
    return b"".join(json_bytes(asdict(row) if is_dataclass(row) else row) + b"\n" for row in rows)


def write_immutable(path: Path, content: bytes) -> None:
    """Create an artifact atomically without ever replacing existing bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == content:
            return
        raise ImmutableArtifactError(f"Refusing to overwrite immutable artifact: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ImmutableArtifactError(f"Refusing to overwrite immutable artifact: {path}")
    finally:
        temporary.unlink(missing_ok=True)


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_ref(root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def resolve_artifact_ref(root: Path, reference: str) -> Path:
    path = Path(reference)
    return path if path.is_absolute() else root / path


def verify_artifact(root: Path, artifact_path: Path, require_manifest: bool = False) -> None:
    """Verify a modern artifact and all source hashes recorded by its sidecar."""
    manifest_path = artifact_path.with_suffix(".manifest.json")
    if not manifest_path.exists():
        if require_manifest:
            raise ArtifactIntegrityError(f"Artifact has no provenance manifest: {artifact_path}")
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactIntegrityError(f"Invalid artifact manifest: {manifest_path}") from exc
    expected = manifest.get("artifact_sha256")
    if expected is None:  # Legacy manifests remain readable.
        if require_manifest:
            raise ArtifactIntegrityError(f"Artifact manifest has no integrity hash: {artifact_path}")
        return
    if not artifact_path.exists() or sha256_path(artifact_path) != expected:
        raise ArtifactIntegrityError(f"Artifact hash mismatch: {artifact_path}")
    for source in manifest.get("sources", []):
        source_path = resolve_artifact_ref(root, source["path"])
        if not source_path.exists() or sha256_path(source_path) != source.get("sha256"):
            raise ArtifactIntegrityError(f"Source artifact hash mismatch: {source_path}")


def verify_lineage(root: Path, artifact_path: Path, expected_sources: dict[str, Path]) -> None:
    manifest_path = artifact_path.with_suffix(".manifest.json")
    if not manifest_path.exists():
        raise ArtifactIntegrityError(f"Artifact has no provenance manifest: {artifact_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = {source["role"]: resolve_artifact_ref(root, source["path"]).resolve() for source in manifest.get("sources", [])}
    for role, expected in expected_sources.items():
        if sources.get(role) != expected.resolve():
            raise ArtifactIntegrityError(f"Artifact lineage mismatch for {role}: {artifact_path}")


def complete_artifact_paths(paths: list[Path]) -> list[Path]:
    """Ignore orphan modern artifacts when at least one manifested artifact exists."""
    manifested = [path for path in paths if path.with_suffix(".manifest.json").exists()]
    return manifested or paths


def write_manifest(
    root: Path,
    artifact_path: Path,
    *,
    artifact_type: str,
    created_at: object,
    record_count: int,
    sources: dict[str, Path],
    methodology: str | None = None,
    parameters: dict[str, object] | None = None,
) -> Path:
    manifest_path = artifact_path.with_suffix(".manifest.json")
    manifest = {
        "schema_version": 1,
        "artifact_type": artifact_type,
        "artifact_path": artifact_ref(root, artifact_path),
        "artifact_sha256": sha256_path(artifact_path),
        "created_at": str(created_at),
        "record_count": record_count,
        "methodology": methodology,
        "parameters": parameters or {},
        "sources": [
            {"role": role, "path": artifact_ref(root, path), "sha256": sha256_path(path)}
            for role, path in sources.items()
        ],
    }
    write_immutable(manifest_path, json_bytes(manifest, pretty=True))
    return manifest_path
