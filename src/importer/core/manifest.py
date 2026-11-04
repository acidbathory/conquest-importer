"""A record of what one migration run created in New Relic, so it can be undone exactly.

Only objects this tool created are listed (never ones that already existed). Rollback deletes
nothing that is not in the manifest, and re-checks each object's id and name first.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Kind = Literal["policy", "condition", "dashboard", "destination", "channel", "workflow"]
# Dependents first: a workflow uses channels, a channel uses a destination, conditions live in a
# policy. Dashboards are independent.
DELETE_ORDER: tuple[Kind, ...] = (
    "workflow",
    "channel",
    "destination",
    "condition",
    "policy",
    "dashboard",
)


class CreatedObject(BaseModel):
    kind: Kind
    id: str
    name: str


class FailedObject(BaseModel):
    """Something New Relic refused to create; kept so the report does not count it as migrated."""

    kind: Kind
    name: str
    reason: str


class Manifest(BaseModel):
    account_id: int
    region: str = "us"
    objects: list[CreatedObject] = []
    failed: list[FailedObject] = []

    def add(self, objects: list[CreatedObject]) -> None:
        known = {(o.kind, o.id) for o in self.objects}
        self.objects += [o for o in objects if (o.kind, o.id) not in known]
        made = {(o.kind, o.name) for o in objects}
        self.failed = [f for f in self.failed if (f.kind, f.name) not in made]

    def add_failed(self, kind: Kind, failures: list[tuple[str, str]]) -> None:
        names = {n for n, _ in failures}
        self.failed = [f for f in self.failed if not (f.kind == kind and f.name in names)]
        self.failed += [FailedObject(kind=kind, name=n, reason=r) for n, r in failures]

    def in_delete_order(self) -> list[CreatedObject]:
        return sorted(self.objects, key=lambda o: DELETE_ORDER.index(o.kind))


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate_json(path.read_text())


def save_manifest(manifest: Manifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=2) + "\n")
