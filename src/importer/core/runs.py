"""Run records: what each guided run created in New Relic, kept so it can be undone later.

One small JSON file per run, in a private folder (0700, files 0600), written atomically. A record
holds type, id, name and account of created objects and never a secret. It is the menu's own
list; the apply commands' `manifest.json` is merged in by `absorb_manifest`.
"""

import os
import re
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from importer.core.manifest import CreatedObject, load_manifest

RUNS_ENV = "IMPORTER_RUNS_DIR"
DEFAULT_RUNS_DIR = Path("out") / "runs"
_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{6}$")
Status = Literal["in-progress", "applied", "rolled-back", "partly-rolled-back"]


class RunRecord(BaseModel):
    run_id: str
    source: str
    account_id: int
    region: str = "us"
    out_dir: str
    started: str = ""
    status: Status = "in-progress"
    objects: list[CreatedObject] = []
    rolled_back: list[str] = []  # ids (kind:id) already deleted
    include_review: bool = False  # notifications: the person chose to include "needs review" plans

    def key(self, o: CreatedObject) -> str:
        return f"{o.kind}:{o.id}"

    def pending(self) -> list[CreatedObject]:
        """Objects created and not yet rolled back."""
        return [o for o in self.objects if self.key(o) not in self.rolled_back]


def default_runs_dir() -> Path:
    return Path(os.environ.get(RUNS_ENV) or DEFAULT_RUNS_DIR)


class RunStore:
    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder if folder is not None else default_runs_dir()

    def _path(self, run_id: str) -> Path:
        if not _RUN_ID.match(run_id):
            raise ValueError(f"not a valid run id: {run_id[:40]!r}")
        return self.folder / f"{run_id}.json"

    def save(self, record: RunRecord) -> None:
        path = self._path(record.run_id)
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.folder.chmod(0o700)
        fd, tmp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")  # created 0600
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(record.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def start(
        self,
        *,
        source: str,
        account_id: int,
        region: str,
        out_dir: Path,
        include_review: bool = False,
    ) -> RunRecord:
        now = datetime.now(UTC)
        record = RunRecord(
            run_id=f"{now:%Y%m%dT%H%M%S%fZ}-{secrets.token_hex(3)}",
            source=source,
            account_id=account_id,
            region=region,
            out_dir=str(out_dir),
            include_review=include_review,
            started=now.isoformat(timespec="seconds"),
        )
        self.save(record)
        return record

    def load(self, run_id: str) -> RunRecord:
        return RunRecord.model_validate_json(self._path(run_id).read_text())

    def list(self) -> list[RunRecord]:
        """All readable records, newest first; damaged files are skipped."""
        records: list[RunRecord] = []
        for path in sorted(self.folder.glob("*.json")) if self.folder.is_dir() else []:
            try:
                records.append(RunRecord.model_validate_json(path.read_text()))
            except (OSError, ValueError):
                continue
        return sorted(records, key=lambda r: r.run_id, reverse=True)

    def absorb_manifest(self, record: RunRecord, manifest_path: Path) -> RunRecord:
        """Merge the objects an apply command recorded; the run counts as applied."""
        manifest = load_manifest(manifest_path)
        if manifest.account_id != record.account_id:
            raise ValueError(
                f"manifest is for account {manifest.account_id}, run is for {record.account_id}"
            )
        known = {record.key(o) for o in record.objects}
        record.objects += [o for o in manifest.objects if record.key(o) not in known]
        record.status = "applied"
        self.save(record)
        return record
