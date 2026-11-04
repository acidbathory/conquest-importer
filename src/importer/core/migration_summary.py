"""What a migration run produced, reduced to numbers and remediation groups for the report.

Reads the folders `migrate` writes (alerts/ir, dashboards/dashboard-ir, export/notifications,
manifest.json). Nothing here talks to a service. Units: one alarm, one dashboard, one notification
subscription. "Migrated" means converted (exact or approximate); in a plan-only run it is not yet
created in New Relic, and the report says which of the two applies.
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from importer.core.dashboard_ir import IRDashboard
from importer.core.ir import Confidence, IRMonitor
from importer.core.manifest import Manifest, load_manifest
from importer.core.remediation import Remedy, remedies_for
from importer.core.stats import reason_pattern
from importer.core.text import printable

Kind = Literal["alarm", "dashboard", "notification"]
KINDS: tuple[Kind, ...] = ("alarm", "dashboard", "notification")
CONVERTED = (Confidence.EXACT, Confidence.APPROXIMATE)
MAX_REASONS = 4


@dataclass
class Asset:
    kind: Kind
    name: str
    source_type: str
    confidence: Confidence
    reasons: list[str] = field(default_factory=list)
    own_steps: list[str] = field(default_factory=list)  # specific to this asset
    detail: str = ""  # for example "5 of 7 widgets converted"

    @property
    def migrated(self) -> bool:
        return self.confidence in CONVERTED


@dataclass
class Group:
    """Assets that share one kind of problem, so the generic steps are written once."""

    title: str
    steps: list[str]
    # Each asset with the extra steps that apply to it beyond the group's (other problems it has).
    items: list[tuple[Asset, list[str]]] = field(default_factory=list)


@dataclass
class Summary:
    assets: list[Asset]
    created: dict[str, int] | None  # object kind -> count, from the manifest; None = plan only
    refused: int = 0  # objects New Relic refused to create (they count as not migrated)

    def of(self, kind: Kind) -> list[Asset]:
        return [a for a in self.assets if a.kind == kind]

    def counts(self, kind: Kind | None = None) -> Counter[Confidence]:
        return Counter(a.confidence for a in self.assets if kind is None or a.kind == kind)

    def headline(self) -> str:
        """One line: total completion across alarms, dashboards and subscriptions."""
        counts = self.counts()
        total = sum(counts.values())
        if total == 0:
            return "Total completion: nothing found to migrate"
        done = counts[Confidence.EXACT] + counts[Confidence.APPROXIMATE]
        if self.created is None:
            return (
                f"Total completion (forecast): {percent(done, total)}% "
                f"({done} of {total} assets can be migrated, {total - done} need manual work; "
                "nothing has been created in New Relic yet)"
            )
        return (
            f"Total completion: {percent(done, total)}% "
            f"({done} of {total} assets migrated, {total - done} need manual work)"
        )

    def groups(self) -> list[Group]:
        """Remediation groups for assets that did not migrate, largest first."""
        found: dict[str, Group] = {}
        for asset in self.assets:
            if asset.migrated:
                continue
            remedies = remedies_for(asset.reasons)
            lead = next((r for r in remedies if not r.secondary), remedies[0] if remedies else None)
            title = lead.title if lead else "Needs manual review"
            steps = list(lead.steps) if lead else [NO_GUIDANCE]
            group = found.setdefault(title, Group(title, steps))
            group.items.append((asset, _extra_steps(asset, lead, remedies)))
        return sorted(found.values(), key=lambda g: (-len(g.items), g.title))

    def review_notes(self) -> list[tuple[str, int]]:
        """Most common caveats on assets that did convert, as (pattern, assets)."""
        seen: Counter[str] = Counter()
        for asset in self.assets:
            if asset.migrated:
                seen.update({reason_pattern(r) for r in asset.reasons})
        return seen.most_common(6)


NO_GUIDANCE = "No automatic guidance exists for this reason. Read it, then rebuild by hand."


def _extra_steps(asset: Asset, lead: Remedy | None, remedies: list[Remedy]) -> list[str]:
    """Steps for one asset that the group's steps do not already cover."""
    steps = list(asset.own_steps)
    for remedy in remedies:
        if remedy is not lead:
            steps += [s for s in remedy.steps if s not in steps and not (lead and s in lead.steps)]
    return steps


def percent(part: int, total: int) -> int | None:
    """Whole-number percentage that never rounds to 0 or 100 unless it truly is."""
    if total == 0:
        return None
    value = round(100 * part / total)
    if part < total:
        value = min(value, 99)
    if part > 0:
        value = max(value, 1)
    return value


def _json_files(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def _alarms(run: Path) -> list[Asset]:
    out = []
    for f in _json_files(run / "alerts" / "ir"):
        m = IRMonitor.model_validate_json(f.read_text())
        out.append(Asset("alarm", m.name, m.source_type, m.confidence, m.reasons, m.remediation))
    return out


def _dashboards(run: Path) -> list[Asset]:
    out = []
    for f in _json_files(run / "dashboards" / "dashboard-ir"):
        d = IRDashboard.model_validate_json(f.read_text())
        failed = [w for w in d.widgets if w.confidence not in CONVERTED]
        reasons = list(d.reasons)
        for w in failed:
            reasons += [f"{printable(w.title, 60)}: {r}" for r in w.reasons]
        detail = f"{len(d.widgets) - len(failed)} of {len(d.widgets)} widgets converted"
        out.append(Asset("dashboard", d.name, "dashboard", d.confidence, reasons, [], detail))
    return out


def _notifications(run: Path) -> list[Asset]:
    from importer.sources.cloudwatch.notifications import load_topics, translate_topic

    folder = run / "export" / "notifications"
    if not folder.is_dir():
        return []
    out = []
    for raw in load_topics(folder):
        topic = translate_topic(raw)
        for i, ch in enumerate(topic.channels, start=1):
            name = f"{topic.name}: {ch.kind.value} #{i}"
            out.append(
                Asset("notification", name, f"{ch.kind.value} subscription", Confidence.EXACT)
            )
        out.extend(
            Asset(
                "notification",
                f"{topic.name}: subscription",
                "subscription",
                Confidence.UNSUPPORTED,
                [reason],
            )
            for reason in topic.unsupported
        )
    return out


def _created(run: Path) -> dict[str, int] | None:
    path = run / "manifest.json"
    if not path.is_file():
        return None
    manifest: Manifest = load_manifest(path)
    return dict(Counter(o.kind for o in manifest.objects))


REFUSED = "could not be created in New Relic"


def _refused(run: Path) -> int:
    path = run / "manifest.json"
    return len(load_manifest(path).failed) if path.is_file() else 0


def _mark_refused(run: Path, assets: list[Asset]) -> None:
    """An asset New Relic refused to create is not migrated, whatever the conversion said."""
    path = run / "manifest.json"
    if not path.is_file():
        return
    asset_kind = {"condition": "alarm", "dashboard": "dashboard"}
    refused = {
        (asset_kind[f.kind], f.name): f.reason
        for f in load_manifest(path).failed
        if f.kind in asset_kind
    }
    for asset in assets:
        reason = refused.get((asset.kind, asset.name))
        if reason is not None and asset.migrated:
            asset.confidence = Confidence.MANUAL
            asset.reasons.insert(0, f"{REFUSED}: {printable(reason, 150)}")


def load_summary(run: Path) -> Summary:
    """Read a `migrate --out` folder. Raises OSError/ValueError on unreadable input."""
    try:
        assets = [*_alarms(run), *_dashboards(run), *_notifications(run)]
        _mark_refused(run, assets)
        return Summary(assets, _created(run), _refused(run))
    except json.JSONDecodeError as exc:
        raise ValueError(f"unreadable JSON in {run}: {exc.msg}") from None
