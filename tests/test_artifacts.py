import json

import pytest

from aifpl.artifacts import ArtifactIntegrityError, verify_artifact, verify_lineage, write_immutable, write_manifest


def test_manifest_supports_external_source_and_verifies_hashes(tmp_path) -> None:
    root = tmp_path / "data"
    external = tmp_path / "aliases.json"
    artifact = root / "catalog.jsonl"
    write_immutable(external, b'{"Alias":"Club"}\n')
    write_immutable(artifact, b'{"id":1}\n')
    write_manifest(
        root, artifact, artifact_type="test", created_at="now", record_count=1,
        sources={"team_aliases": external},
    )

    verify_artifact(root, artifact)
    verify_lineage(root, artifact, {"team_aliases": external})
    manifest = json.loads(artifact.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert manifest["sources"][0]["path"] == str(external.resolve())


def test_artifact_hash_tampering_fails_closed(tmp_path) -> None:
    root = tmp_path / "data"
    source = root / "source.json"
    artifact = root / "catalog.jsonl"
    write_immutable(source, b'{}')
    write_immutable(artifact, b'{"id":1}\n')
    write_manifest(root, artifact, artifact_type="test", created_at="now", record_count=1, sources={"source": source})
    artifact.write_text('{"id":2}\n', encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError, match="hash mismatch"):
        verify_artifact(root, artifact)


def test_explicit_artifact_requires_manifest(tmp_path) -> None:
    artifact = tmp_path / "legacy.jsonl"
    artifact.write_text('{"id":1}\n', encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError, match="no provenance manifest"):
        verify_artifact(tmp_path, artifact, require_manifest=True)
