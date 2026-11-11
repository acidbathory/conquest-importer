"""The one-pass source flow of the menu: what to read and convert per source, and what it found.

`build_kinds` names, for one source, the existing commands that read and convert each kind
(alerts, notifications, dashboards). `summarise` then counts the real converter outputs on disk:
what the apply commands would create, and the items the remediation reader lists as needing
attention. Nothing here talks to a network; the numbers are never estimated.
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from importer.core.apply import load_plan
from importer.core.apply_dashboards import load_dashboard_plan
from importer.core.notification_ir import NotificationPlan
from importer.core.remediation_plan import build_plan, read_items

Step = list[str]
KINDS = ("alerts", "notifications", "dashboards")  # the safe order to create in
PLANS = "notification-plans"
LABEL = {"alerts": "alert conditions", "notifications": "notification workflows"}
LABEL |= {"dashboards": "dashboards"}
STATE = {"alerts": "disabled", "notifications": "disabled", "dashboards": "private"}
APPLY = {
    "alerts": "apply",
    "notifications": "apply-notifications",
    "dashboards": "apply-dashboards",
}


@dataclass(frozen=True)
class KindPlan:
    kind: str
    read: list[Step]  # read-only commands (may be empty: the data was already read)
    convert: list[Step]  # local commands
    creates: bool = True  # False: planned only, there is nothing to create


@dataclass(frozen=True)
class Layout:
    folder: Path

    @property
    def export(self) -> Path:
        return self.folder / "export"

    @property
    def converted(self) -> Path:
        return self.folder / "converted"

    def raw(self, kind: str) -> Path:
        return self.export / kind

    def out(self, kind: str) -> Path:
        return self.converted / kind

    @property
    def remediation(self) -> Path:
        return self.folder / "remediation"

    @property
    def datapath(self) -> Path:
        return self.folder / "datapath"

    @property
    def triage(self) -> Path:
        return self.folder / "triage"

    @property
    def quickstarts(self) -> Path:
        return self.folder / "quickstarts"


def _s(*parts: object) -> Step:
    return [str(p) for p in parts]


def build_kinds(key: str, source: str, extract: Step, lay: Layout) -> list[KindPlan]:
    """The read and convert commands per kind. `key` is the menu number of the source."""
    alerts_raw, alerts = lay.raw("alerts"), lay.out("alerts")
    read_alerts = [[*extract, "--out", str(alerts_raw)]]
    if key == "1":  # GCP: one extract holds policies, channels and dashboards
        convert_alerts = _s("convert", "--source", source, "--in", alerts_raw / "policies")
        notes = _s("convert-gcp-notifications", "--channels", alerts_raw / "channels")
        notes_plan = KindPlan(
            "notifications",
            [],
            [[*notes, "--alerts", str(alerts), "--out", str(lay.out("notifications"))]],
            creates=False,
        )
        dash = _s("convert-dashboards", "--source", "gcp", "--in", alerts_raw / "dashboards")
        dash_plan = KindPlan("dashboards", [], [[*dash, "--out", str(lay.out("dashboards"))]])
    elif key == "2":  # AWS
        convert_alerts = _s("convert", "--source", source, "--in", alerts_raw / "alarms")
        topics = _s("convert-notifications", "--topics", alerts_raw / "notifications")
        notes_plan = KindPlan(
            "notifications",
            [],
            [[*topics, "--alerts", str(alerts), "--out", str(lay.out("notifications"))]],
        )
        dash = _s("convert-dashboards", "--in", alerts_raw / "dashboards")
        dash_plan = KindPlan("dashboards", [], [[*dash, "--out", str(lay.out("dashboards"))]])
    elif key == "3":  # Grafana
        convert_alerts = _s("convert", "--source", source, "--in", alerts_raw)
        raw_n, out_n = lay.raw("notifications"), lay.out("notifications")
        notes_plan = KindPlan(
            "notifications",
            [_s("extract-grafana-notifications", "--out", raw_n)],
            [
                _s(
                    "convert-grafana-notifications",
                    "--in",
                    raw_n,
                    "--alerts",
                    alerts,
                    "--out",
                    out_n,
                )
            ],
        )
        raw_d, out_d = lay.raw("dashboards"), lay.out("dashboards")
        dash_plan = KindPlan(
            "dashboards",
            [_s("extract-grafana-dashboards", "--out", raw_d)],
            [_s("convert-grafana-dashboards", "--in", raw_d, "--out", out_d)],
        )
    elif key == "10":  # Azure: one extract holds alert rules, action groups and processing rules
        convert_alerts = _s("convert", "--source", source, "--in", alerts_raw)
        return [KindPlan("alerts", read_alerts, [[*convert_alerts, "--out", str(alerts)]])]
    else:  # Dynatrace; dashboards are planned only
        convert_alerts = _s("convert", "--source", source, "--in", alerts_raw)
        topics = _s("convert-notifications", "--source", "dynatrace", "--topics", alerts_raw)
        notes_plan = KindPlan(
            "notifications",
            [],
            [[*topics, "--alerts", str(alerts), "--out", str(lay.out("notifications"))]],
        )
        raw_d, out_d = lay.raw("dashboards"), lay.out("dashboards")
        dash_plan = KindPlan(
            "dashboards",
            [_s("extract-dynatrace-inventory", "--out", raw_d)],
            [_s("plan-dynatrace-dashboards", "--in", raw_d, "--out", out_d)],
            creates=False,
        )
    alert_plan = KindPlan("alerts", read_alerts, [[*convert_alerts, "--out", str(alerts)]])
    return [alert_plan, notes_plan, dash_plan]


def extra_plans(key: str, lay: Layout) -> list[Step]:
    """Grafana only: the data-path guide, the dashboard ranking and the quickstart match."""
    if key != "3":
        return []
    raw_d = lay.raw("dashboards")
    return [
        _s(
            "plan-grafana-datapath", "--export", lay.raw("notifications"),
            "--alerts", lay.out("alerts"), "--dashboards", lay.out("dashboards"),
            "--out", lay.datapath,
        ),
        _s("triage-grafana-dashboards", "--in", raw_d, "--out", lay.triage),
        _s("plan-grafana-quickstarts", "--in", raw_d, "--out", lay.quickstarts),
    ]  # fmt: skip


@dataclass
class Found:
    """What one converted kind holds, counted from its files."""

    create: int = 0
    attention: int = 0
    present: bool = False


@dataclass
class Summary:
    kinds: dict[str, Found] = field(default_factory=dict)
    reasons: list[tuple[str, int]] = field(default_factory=list)  # top reasons, most items first

    def create(self, kind: str) -> int:
        return self.kinds[kind].create if kind in self.kinds else 0

    @property
    def total(self) -> int:
        return sum(f.create for f in self.kinds.values())


def _notification_plans(folder: Path) -> list[NotificationPlan]:
    out: list[NotificationPlan] = []
    for path in sorted((folder / PLANS).glob("*.json")):
        try:
            out.append(NotificationPlan.model_validate_json(path.read_text()))
        except (OSError, ValueError):
            continue
    return out


def planned_names(lay: Layout) -> dict[str, list[str]]:
    """The names the apply commands would create, per kind, from the converter's own files."""
    out: dict[str, list[str]] = {k: [] for k in KINDS}
    try:
        out["alerts"] = [i.name for i in load_plan(lay.out("alerts") / "ir").items]
        out["dashboards"] = [
            i.name for i in load_dashboard_plan(lay.out("dashboards") / "dashboard-ir").items
        ]
    except (OSError, ValueError):
        pass
    out["notifications"] = [
        p.workflow.name
        for p in _notification_plans(lay.out("notifications"))
        if p.workflow is not None and not p.needs_review
    ]
    return out


def _count_create(kind: str, folder: Path) -> int:
    """How many objects the apply command would create from this folder (its own readers)."""
    try:
        if kind == "alerts":
            return len(load_plan(folder / "ir").items)
        if kind == "dashboards":
            return len(load_dashboard_plan(folder / "dashboard-ir").items)
    except (OSError, ValueError):
        return 0
    return sum(p.workflow is not None and not p.needs_review for p in _notification_plans(folder))


def _attention(kind: str, folder: Path) -> int:
    """Unconverted items listed by the remediation reader, plus workflows held back for review."""
    try:
        count = sum(i.count for i in read_items(folder))
    except (OSError, ValueError):
        count = 0
    if kind == "notifications":
        count += sum(p.workflow is not None and p.needs_review for p in _notification_plans(folder))
    return count


def summarise(lay: Layout, converted: list[KindPlan], top: int = 5) -> Summary:
    """Count each converted kind and rank the reasons from the remediation reader."""
    result = Summary()
    for kp in converted:
        folder = lay.out(kp.kind)
        found = Found(present=folder.is_dir())
        if found.present:
            found.create = _count_create(kp.kind, folder) if kp.creates else 0
            found.attention = _attention(kp.kind, folder)
        result.kinds[kp.kind] = found
    try:
        groups = build_plan(read_items(lay.converted)).groups
    except (OSError, ValueError):
        groups = []
    result.reasons = [(g.title, g.count) for g in groups[:top]]
    return result


def read_int(path: Path, *keys: str) -> int | None:
    """A whole number from a JSON file written by a plan command, or None."""
    try:
        data: object = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    for key in keys:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data if isinstance(data, int) and not isinstance(data, bool) else None


def triage_buckets(path: Path) -> Counter[str]:
    """How many dashboards fall in each bucket of triage-report.json; empty when unreadable."""
    try:
        data: object = json.loads(path.read_text())
        rows = data["dashboards"] if isinstance(data, dict) else []
        return Counter(str(r["bucket"]) for r in rows if isinstance(r, dict) and "bucket" in r)
    except (OSError, ValueError, KeyError, TypeError):
        return Counter()
