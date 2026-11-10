"""Shared test doubles for the menu: real local converters, fake read and create."""

import shutil
from pathlib import Path

from importer.cli import app
from importer.commands.menu import make_runner
from importer.core.manifest import CreatedObject, Manifest, save_manifest
from importer.core.notification_ir import NotificationPlan, PlannedWorkflow, plan_json

FIX = Path(__file__).parent / "fixtures"
# What each extract command "reads": a fixture folder copied to --out (nothing leaves the machine).
READS = {
    "extract-grafana": (FIX / "grafana", ""),
    "extract-grafana-dashboards": (FIX / "grafana-dashboards", ""),
    "extract-grafana-notifications": (FIX / "grafana-notifications", ""),
    "extract-gcp": (FIX / "gcp", "policies"),
    "extract-dynatrace": (FIX / "dynatrace", ""),
    "extract-dynatrace-inventory": (FIX / "dynatrace-dashboards", ""),
}
# What a successful create of each kind records, like the apply commands' manifests.
CREATED = {
    "apply": ("condition", "10", "ckg-grafana-cpu"),
    "apply-dashboards": ("dashboard", "g1", "ckg-Checkout latency"),
    "apply-notifications": ("workflow", "w1", "Imported:route 1"),
}


class HybridRunner:
    """Real local commands (convert, plan-*, triage, report); fake read, doctor and create."""

    def __init__(
        self,
        fail: set[str] | None = None,
        account: int = 7,
        existing: dict[str, int] | None = None,
        no_create: set[str] | None = None,
    ) -> None:
        self.existing = existing or {}  # apply command -> objects that "already existed"
        self.no_create = no_create or set()  # apply commands that create nothing
        self.calls: list[list[str]] = []
        self.fail = fail or set()
        self.account = account
        self.real = make_runner(app)

    def __call__(self, args: list[str]) -> int:
        self.calls.append(args)
        name = args[0]
        if name in self.fail:
            return 1
        if name == "doctor":
            return 0
        if name.startswith("extract-"):
            fixture, sub = READS.get(name, (FIX / "grafana", ""))
            shutil.copytree(fixture, Path(args[args.index("--out") + 1]) / sub)
            return 0
        if name in CREATED and "--apply" in args:
            kind, oid, label = CREATED[name]
            path = Path(args[args.index("--manifest") + 1])
            obj = {"kind": kind, "id": oid, "name": label}
            made = [] if name in self.no_create else [CreatedObject.model_validate(obj)]
            old = {kind: self.existing[name]} if name in self.existing else {}
            save_manifest(Manifest(account_id=self.account, objects=made, existing=old), path)
            return 0
        code = self.real(args)
        if name == "convert-grafana-notifications" and code == 0:
            self._ensure_one_plan(Path(args[args.index("--out") + 1]))
        return code

    @staticmethod
    def _ensure_one_plan(out: Path) -> None:
        """The notification fixture has no rule its policies can route, so the real converter
        plans no workflow; add one plan by hand so the create path can be exercised."""
        folder = out / "notification-plans"
        if any(folder.glob("*.json")):
            return
        folder.mkdir(parents=True, exist_ok=True)
        flow = PlannedWorkflow(
            name="Imported:route 1", condition_names=["ckg-grafana-cpu"], destination_indexes=[]
        )
        plan = NotificationPlan(topic="route 1", destinations=[], workflow=flow)
        (folder / "topic-0001.json").write_text(plan_json(plan))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def writes(self) -> list[str]:
        return [c[0] for c in self.calls if "--apply" in c]
