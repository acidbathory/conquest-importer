"""The scoreboard: one honest table of how far the migration has actually got.

Reads only files that already exist (converter output folders, an optional `parity.json` in them,
the tracked live-evidence file, `docs/improvement/baseline.json`) and never calls anything. A
placeholder widget ("Not migrated") is counted as a placeholder, never as converted. Names and
paths in those files are untrusted: metric keys are reduced to a safe alphabet and every string
that reaches the markdown goes through `md_cell`.
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from importer.core.report import md_cell
from importer.core.text import printable

MAX_FILES = 5000
MAX_FILE_BYTES = 20_000_000
MAX_HISTORY_ROWS = 10_000
KEY_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
TEST_DEF = re.compile(r"^\s*(?:async\s+)?def (test_\w*)\(", re.MULTILINE)
CONVERTED = ("exact", "approximate")
LOWER_IS_BETTER = ("alerts_manual", "alerts_unsupported", "widgets_placeholders")
HIGHER_IS_BETTER = ("alerts_converted", "widgets_converted", "verified")
BAD_VERDICTS = frozenset({"mismatch", "mismatched", "diverge", "diverged", "fail", "failed"})
GOOD_VERDICTS = frozenset({"match", "matched", "verified", "pass", "passed"})
# Sources whose converted items without a `data_path` in the saved IR are native (the field is
# left out of the file for them). Other sources are not counted by data path at all.
DEFAULT_DATA_PATH = {"gcp": "native"}
EVIDENCE_FIELDS = ("id", "source", "claim", "date", "method", "evidence")


def _key(text: object) -> str:
    return KEY_UNSAFE.sub("_", str(text))[:40] or "unknown"


def direction(metric: str) -> int:
    """+1 if a higher value is progress, -1 if lower is, 0 if the metric is only information."""
    name = metric.rsplit("/", 1)[-1]
    if name in LOWER_IS_BETTER or (name.startswith("parity_") and name[7:] in BAD_VERDICTS):
        return -1
    if name in HIGHER_IS_BETTER or (name.startswith("parity_") and name[7:] in GOOD_VERDICTS):
        return 1
    return 0


def _read_json(path: Path, warnings: list[str]) -> Any:
    try:
        if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("is a link or too large")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warnings.append(f"{printable(path.name, 60)} skipped: {printable(str(exc), 80)}")
        return None


def _bump(metrics: dict[str, int], name: str, by: int = 1) -> None:
    metrics[name] = metrics.get(name, 0) + by


def _count_data_path(
    metrics: dict[str, int], prefix: str, noun: str, source: str, data: dict[str, Any]
) -> None:
    """Count a converted item by the data path it needs; `datapath_known` leaves out `unknown`."""
    path = data.get("data_path") or DEFAULT_DATA_PATH.get(source)
    if not isinstance(path, str):
        return
    _bump(metrics, f"{prefix}{noun}_datapath_{_key(path)}")
    if path != "unknown":
        _bump(metrics, f"{prefix}{noun}_datapath_known")


def _scan_alerts(folder: Path, metrics: dict[str, int], warnings: list[str]) -> None:
    ir = folder / "ir"
    if ir.is_symlink() or not ir.is_dir():
        return
    for path in sorted(ir.glob("*.json"))[:MAX_FILES]:
        data = _read_json(path, warnings)
        if data is None:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("confidence"), str):
            warnings.append(f"{printable(path.name, 60)} skipped: not a converted monitor")
            continue
        source = _key(data.get("source_system", "unknown"))
        prefix = f"out/{source}/"
        confidence = data["confidence"]
        if confidence in CONVERTED:
            _bump(metrics, prefix + "alerts_converted")
            _bump(metrics, prefix + f"alerts_{confidence}")
            _count_data_path(metrics, prefix, "alerts", source, data)
        elif confidence in ("manual", "unsupported"):
            _bump(metrics, prefix + f"alerts_{confidence}")
        else:
            warnings.append(f"{printable(path.name, 60)} skipped: unknown confidence")


def widget_class(kind: object) -> str:
    """How the scoreboard counts a converted widget: "converted" (a real chart), "placeholder"
    (a "Not migrated" note, never converted) or "other" (text, headings). One rule everywhere."""
    if kind == "chart":
        return "converted"
    return "placeholder" if kind == "unsupported" else "other"


def _scan_dashboards(folder: Path, metrics: dict[str, int], warnings: list[str]) -> None:
    ir = folder / "dashboard-ir"
    if ir.is_symlink() or not ir.is_dir():
        return
    for path in sorted(ir.glob("*.json"))[:MAX_FILES]:
        data = _read_json(path, warnings)
        if data is None:
            continue
        widgets = data.get("widgets") if isinstance(data, dict) else None
        if not isinstance(widgets, list):
            warnings.append(f"{printable(path.name, 60)} skipped: not a converted dashboard")
            continue
        prefix = f"out/{_key(data.get('source_system', 'unknown'))}/"
        _bump(metrics, prefix + "dashboards")
        for widget in widgets:
            kind = widget.get("kind") if isinstance(widget, dict) else None
            if widget_class(kind) == "converted":
                _bump(metrics, prefix + "widgets_converted")
                _count_data_path(
                    metrics, prefix, "widgets", _key(data.get("source_system", "unknown")), widget
                )
            elif widget_class(kind) == "placeholder":
                _bump(metrics, prefix + "widgets_placeholders")  # never counted as converted


def _verdicts(data: Any) -> dict[str, int] | None:
    """Verdict counts from a parity file: {"verdicts": {...}}, {"items": [...]} or a list."""
    if isinstance(data, dict) and isinstance(data.get("verdicts"), dict):
        counts = data["verdicts"]
        if all(isinstance(v, int) and not isinstance(v, bool) for v in counts.values()):
            return {_key(k).lower(): v for k, v in counts.items()}
        return None
    items = data.get("items", data.get("results")) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    out: dict[str, int] = {}
    for item in items:
        verdict = item.get("verdict") if isinstance(item, dict) else None
        if isinstance(verdict, str):
            _bump(out, _key(verdict).lower())
    return out


def _scan_parity(folder: Path, metrics: dict[str, int], warnings: list[str]) -> None:
    path = folder / "parity.json"
    if not path.is_file():
        return
    data = _read_json(path, warnings)
    if data is None:
        return
    counts = _verdicts(data)
    if not counts:
        warnings.append("parity.json skipped: no verdict counts or items with a verdict")
        return
    source = "unknown"
    if isinstance(data, dict) and isinstance(data.get("source"), str):
        source = _key(data["source"])
    else:
        known = sorted({k.split("/")[1] for k in metrics if k.startswith("out/")})
        source = known[0] if len(known) == 1 else source
    for verdict, n in counts.items():
        _bump(metrics, f"out/{source}/parity_{verdict}", n)


def _scan_roots(folders: list[Path]) -> list[Path]:
    """Each folder and the folders directly below it, once each, links not followed."""
    seen: set[Path] = set()
    roots: list[Path] = []
    for folder in folders:
        candidates = [folder]
        candidates += sorted(p for p in folder.iterdir() if p.is_dir() and not p.is_symlink())
        for one in candidates:
            real = one.resolve()
            if real not in seen:
                seen.add(real)
                roots.append(one)
    return roots


def _remediation(folders: list[Path]) -> tuple[list[dict[str, Any]], str]:
    from importer.core.remediation_plan import build_plan, read_items

    resolved = {f.resolve() for f in folders}
    tops = [f for f in folders if f.resolve().parent not in resolved]
    try:
        items = [i for f in tops for i in read_items(f)]
        if not items:
            return [], "no unconverted items found"
        top = build_plan(items).top_fixes(5)
    except (ValueError, OSError) as exc:
        return [], f"not computed: {printable(str(exc), 100)}"
    return [{"title": t.title, "unlocks": t.unlocks, "affects": t.affects} for t in top], ""


def _evidence(path: Path, metrics: dict[str, int], warnings: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"verified": [], "not_proven": []}
    if not path.is_file():
        warnings.append(f"evidence file {printable(path.name, 60)} not found")
        return result
    data = _read_json(path, warnings)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        warnings.append(f"evidence file {printable(path.name, 60)} has no items list")
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        needed = EVIDENCE_FIELDS if status == "verified" else ("id", "source", "claim")
        if status not in ("verified", "not_proven") or not all(
            isinstance(item.get(f), str) and item[f] for f in needed
        ):
            warnings.append(f"evidence item {printable(str(item.get('id')), 40)} is incomplete")
            continue
        entry = {f: item.get(f, "") for f in EVIDENCE_FIELDS}
        if status == "verified":
            result["verified"].append(entry)
            _bump(metrics, f"live/{_key(item['source'])}/verified")
        else:
            result["not_proven"].append(entry)
    return result


def _count_tests(root: Path) -> int:
    total = 0
    for path in sorted((root / "tests").rglob("test_*.py"))[:MAX_FILES]:
        if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        total += len(TEST_DEF.findall(path.read_text(encoding="utf-8", errors="replace")))
    return total


def _baseline(root: Path, metrics: dict[str, int], warnings: list[str]) -> None:
    path = root / "docs" / "improvement" / "baseline.json"
    if not path.is_file():
        return
    data = _read_json(path, warnings)
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, dict):
        return
    for name, entry in sources.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("by_confidence"), dict):
            continue
        by = entry["by_confidence"]
        prefix = f"fixtures/{_key(name)}/"
        converted = sum(v for k, v in by.items() if k in CONVERTED and isinstance(v, int))
        metrics[prefix + "alerts_converted"] = converted
        for level in ("manual", "unsupported"):
            if isinstance(by.get(level), int):
                metrics[prefix + f"alerts_{level}"] = by[level]


def gather(
    folders: list[Path], root: Path | None, evidence: Path | None, today: str
) -> dict[str, Any]:
    """Collect every metric. Problems with one input become warnings, never a crash."""
    metrics: dict[str, int] = {}
    warnings: list[str] = []
    usable = []
    for folder in folders:
        if folder.is_dir():
            usable.append(folder)
        else:
            warnings.append(f"{printable(folder.name, 60)} is not a folder; skipped")
    for one in _scan_roots(usable):
        _scan_alerts(one, metrics, warnings)
        _scan_dashboards(one, metrics, warnings)
        _scan_parity(one, metrics, warnings)
    remediation, note = _remediation(usable) if usable else ([], "no output folder given")
    live = (
        _evidence(evidence, metrics, warnings) if evidence else {"verified": [], "not_proven": []}
    )
    if root is not None:
        _baseline(root, metrics, warnings)
        if (root / "tests").is_dir():
            metrics["tests/test_functions"] = _count_tests(root)
    return {
        "measured": today,
        "metrics": dict(sorted(metrics.items())),
        "remediation": remediation,
        "remediation_note": note,
        "live": live,
        "warnings": warnings,
        "ci": "not fetched (use --ci)",
    }


@dataclass(frozen=True)
class Delta:
    metric: str
    before: int | None
    after: int | None
    verdict: str


def compare(previous: dict[str, Any], current: dict[str, Any]) -> list[Delta]:
    before: dict[str, int] = previous.get("metrics", {})
    after: dict[str, int] = current.get("metrics", {})
    deltas: list[Delta] = []
    for metric in sorted(before.keys() | after.keys()):
        b, a = before.get(metric), after.get(metric)
        if b is None:
            verdict = "NEW"
        elif a is None:
            verdict = "GONE"
        elif a == b:
            verdict = "NOT MOVED"
        else:
            sign = direction(metric)
            verdict = (
                "MOVED (info)"
                if sign == 0
                else ("MOVED (better)" if (a - b) * sign > 0 else "MOVED (worse)")
            )
        deltas.append(Delta(metric, b, a, verdict))
    return deltas


def verdict_line(deltas: list[Delta], previous: dict[str, Any]) -> str:
    since = printable(str(previous.get("measured", "the previous run")), 40)
    better = sum(d.verdict == "MOVED (better)" for d in deltas)
    worse = sum(d.verdict == "MOVED (worse)" for d in deltas)
    changed = sum(d.verdict in ("NEW", "GONE") and direction(d.metric) != 0 for d in deltas)
    if better or worse or changed:
        extra = f" {changed} outcome metric(s) are new or gone." if changed else ""
        return f"{better} outcome metric(s) moved better, {worse} worse, since {since}.{extra}"
    info = any(d.verdict.startswith("MOVED") or d.verdict in ("NEW", "GONE") for d in deltas)
    note = " Tests and other info changed, which is not progress." if info else ""
    return f"No outcome metric moved since {since}.{note} Propose a different lever."


@dataclass(frozen=True)
class HistoryRow:
    date: str
    metrics: dict[str, int]


def append_history(path: Path, date: str, metrics: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    cells = ";".join(f"{k}={v}" for k, v in sorted(metrics.items()))
    with path.open("a", encoding="utf-8") as handle:
        if new:
            handle.write("date,metrics\n")
        handle.write(f"{_key(date)},{cells}\n")


def read_history(path: Path) -> list[HistoryRow]:
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        return []
    rows: list[HistoryRow] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[1:MAX_HISTORY_ROWS]:
        date, comma, cells = line.partition(",")
        if not comma or not date:
            continue
        metrics: dict[str, int] = {}
        for pair in cells.split(";"):
            name, eq, value = pair.partition("=")
            if eq and value.lstrip("-").isdigit():
                metrics[name] = int(value)
        rows.append(HistoryRow(date, metrics))
    return rows


def stalled_since(rows: list[HistoryRow], k: int) -> str | None:
    """The date the outcome metrics last changed, if they have not changed for `k` cycles."""

    def outcome(row: HistoryRow) -> dict[str, int]:
        return {m: v for m, v in row.metrics.items() if direction(m) != 0}

    if not rows:
        return None
    start = len(rows) - 1
    while start > 0 and outcome(rows[start - 1]) == outcome(rows[-1]):
        start -= 1
    return rows[start].date if len(rows) - 1 - start >= k else None


def stall_message(rows: list[HistoryRow], k: int) -> str:
    since = stalled_since(rows, k)
    if since is None:
        return ""
    return (
        f"STALLED: no outcome metric has moved over the last {k} cycles (unchanged since "
        f"{printable(since, 40)}). Say so plainly and propose a different lever."
    )


def ci_status() -> str:
    """Latest CI run via the GitHub CLI, if installed. The only networked call, only on --ci."""
    gh = shutil.which("gh")
    if gh is None:
        return "unknown (gh not installed)"
    try:
        done = subprocess.run(  # noqa: S603
            [gh, "run", "list", "--limit", "1", "--json", "status,conclusion,headSha,createdAt"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if done.returncode != 0:
            return "unknown (gh run list failed)"
        run = json.loads(done.stdout)[0]
        state = run["conclusion"] or run["status"]
        sha, when = str(run["headSha"])[:7], str(run["createdAt"])
        return f"{printable(str(state), 20)} ({printable(sha, 7)}, {printable(when, 30)})"
    except (OSError, ValueError, LookupError, TypeError, subprocess.SubprocessError):
        return "unknown (gh run list failed)"


def render_markdown(board: dict[str, Any], deltas: list[Delta] | None = None) -> str:
    lines = [f"# Scoreboard ({md_cell(str(board['measured']), 40)})", ""]
    lines.append(
        "Placeholders are never counted as converted. Live evidence is only what is "
        "recorded in `docs/improvement/live-evidence.json`."
    )
    lines += ["", f"CI: {md_cell(str(board['ci']), 80)}", ""]
    by_group: dict[str, list[tuple[str, int]]] = {}
    for metric, value in board["metrics"].items():
        group, _, name = metric.rpartition("/")
        by_group.setdefault(group, []).append((name, value))
    lines += ["## Metrics", "", "| Scope | Metric | Value |", "|---|---|---:|"]
    for group, entries in by_group.items():
        lines += [f"| {md_cell(group)} | {md_cell(n)} | {v} |" for n, v in entries]
    lines += ["", "## Remediation top 5", ""]
    top = board["remediation"]
    if top:
        lines += [
            f"{i}. {md_cell(t['title'], 150)}: unlocks {t['unlocks']} of {t['affects']}"
            for i, t in enumerate(top, 1)
        ]
    else:
        lines.append(f"None ({md_cell(str(board['remediation_note']), 100)}).")
    lines += ["", "## Live evidence", ""]
    for item in board["live"]["verified"]:
        lines.append(
            f"- verified {md_cell(item['date'], 20)}: {md_cell(item['claim'], 150)} "
            f"({md_cell(item['method'], 80)}; {md_cell(item['evidence'], 100)})"
        )
    for item in board["live"]["not_proven"]:
        lines.append(f"- NOT PROVEN: {md_cell(item['claim'], 150)}")
    if deltas is not None:
        lines += [
            "",
            "## Compared with the previous run",
            "",
            "| Metric | Before | After | Verdict |",
            "|---|---:|---:|---|",
        ]
        lines += [
            f"| {md_cell(d.metric)} | {'' if d.before is None else d.before} | "
            f"{'' if d.after is None else d.after} | {d.verdict} |"
            for d in deltas
        ]
    if board["warnings"]:
        lines += ["", "## Warnings", ""] + [f"- {md_cell(w, 200)}" for w in board["warnings"][:50]]
    return "\n".join(lines) + "\n"
