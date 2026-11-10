"""Run records: private permissions, atomic writes, listing, merging a manifest."""

import stat
from pathlib import Path

import pytest

from importer.core.manifest import CreatedObject, Manifest, save_manifest
from importer.core.runs import RunRecord, RunStore


def obj(kind: str = "condition", id_: str = "1", name: str = "ckg-a") -> CreatedObject:
    return CreatedObject.model_validate({"kind": kind, "id": id_, "name": name})


def test_save_uses_private_permissions(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    record = store.start(source="gcp", account_id=7, region="us", out_dir=tmp_path / "o")
    assert stat.S_IMODE((tmp_path / "runs").stat().st_mode) == 0o700
    path = tmp_path / "runs" / f"{record.run_id}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list((tmp_path / "runs").glob("*.tmp"))


def test_roundtrip_and_listing_newest_first(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    first = store.start(source="gcp", account_id=7, region="us", out_dir=tmp_path)
    second = store.start(source="aws", account_id=7, region="us", out_dir=tmp_path)
    assert [r.run_id for r in store.list()] == [second.run_id, first.run_id]
    assert store.load(first.run_id).source == "gcp"


def test_run_id_is_validated(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with pytest.raises(ValueError, match="run id"):
        store.load("../etc/passwd")


def test_absorb_manifest_marks_applied_and_dedupes(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    record = store.start(source="gcp", account_id=7, region="us", out_dir=tmp_path)
    manifest = Manifest(account_id=7, objects=[obj()])
    save_manifest(manifest, tmp_path / "manifest.json")
    store.absorb_manifest(record, tmp_path / "manifest.json")
    store.absorb_manifest(record, tmp_path / "manifest.json")
    again = store.load(record.run_id)
    assert again.status == "applied"
    assert len(again.objects) == 1


def test_absorb_refuses_other_account(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    record = store.start(source="gcp", account_id=7, region="us", out_dir=tmp_path)
    save_manifest(Manifest(account_id=8, objects=[obj()]), tmp_path / "manifest.json")
    with pytest.raises(ValueError, match="account"):
        store.absorb_manifest(record, tmp_path / "manifest.json")


def test_record_model_defaults() -> None:
    record = RunRecord(run_id="r", source="gcp", account_id=1, region="us", out_dir="x")
    assert record.status == "in-progress"
    assert record.pending() == []
