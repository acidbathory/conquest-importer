"""Scoreboard: reads converter output folders and tracked evidence, never counts a placeholder as
converted, compares two runs and says plainly when nothing moved."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core import scoreboard as sb


def _monitor(path: Path, name: str, confidence: str, source: str = "grafana") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{name}.json").write_text(
        json.dumps({"source_system": source, "name": name, "confidence": confidence})
    )


def _dashboard(path: Path, name: str, kinds: list[str], source: str = "grafana") -> None:
    path.mkdir(parents=True, exist_ok=True)
    widgets = [{"kind": k, "title": f"w{i}", "confidence": "exact"} for i, k in enumerate(kinds)]
    (path / f"{name}.json").write_text(
        json.dumps({"source_system": source, "name": name, "widgets": widgets})
    )


@pytest.fixture
def out(tmp_path: Path) -> Path:
    folder = tmp_path / "out" / "grafana"
    _monitor(folder / "ir", "a", "exact")
    _monitor(folder / "ir", "b", "approximate")
    _monitor(folder / "ir", "c", "manual")
    _monitor(folder / "ir", "d", "unsupported")
    _dashboard(folder / "dashboard-ir", "d1", ["chart", "chart", "unsupported", "markdown"])
    return folder


def test_alerts_and_widgets_are_counted_per_source(out: Path) -> None:
    board = sb.gather([out], root=None, evidence=None, today="2026-11-10")
    m = board["metrics"]
    assert m["out/grafana/alerts_converted"] == 2
    assert m["out/grafana/alerts_approximate"] == 1
    assert m["out/grafana/alerts_manual"] == 1
    assert m["out/grafana/alerts_unsupported"] == 1
    assert m["out/grafana/widgets_converted"] == 2


def test_a_placeholder_is_never_converted(out: Path) -> None:
    m = sb.gather([out], root=None, evidence=None, today="2026-11-10")["metrics"]
    assert m["out/grafana/widgets_placeholders"] == 1
    assert m["out/grafana/widgets_converted"] == 2  # markdown text panels count as neither


def test_parent_folder_of_several_outputs_is_read_once(tmp_path: Path, out: Path) -> None:
    board = sb.gather([tmp_path / "out", out], root=None, evidence=None, today="d")
    assert board["metrics"]["out/grafana/alerts_manual"] == 1


def test_parity_verdicts_are_counted(out: Path) -> None:
    (out / "parity.json").write_text(
        json.dumps({"items": [{"verdict": "match"}, {"verdict": "Match"}, {"verdict": "mismatch"}]})
    )
    m = sb.gather([out], root=None, evidence=None, today="d")["metrics"]
    assert m["out/grafana/parity_match"] == 2
    assert m["out/grafana/parity_mismatch"] == 1


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1, 2]",
        '{"items": "x"}',
        '{"items": [1, {"verdict": 5}]}',
        '{"verdicts": {"match": "x"}}',
    ],
)
def test_bad_parity_file_is_a_warning_not_a_crash(out: Path, content: str) -> None:
    (out / "parity.json").write_text(content)
    board = sb.gather([out], root=None, evidence=None, today="d")
    assert not any(k.startswith("out/grafana/parity_") for k in board["metrics"])
    assert board["warnings"]


def test_parity_verdict_counts_form_is_accepted(out: Path) -> None:
    (out / "parity.json").write_text(json.dumps({"verdicts": {"match": 4, "mismatch": 1}}))
    m = sb.gather([out], root=None, evidence=None, today="d")["metrics"]
    assert m["out/grafana/parity_match"] == 4


def test_missing_and_odd_inputs_are_warnings(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x")
    board = sb.gather(
        [tmp_path / "nope", tmp_path / "file.txt"], root=None, evidence=None, today="d"
    )
    assert len(board["warnings"]) == 2
    assert board["metrics"] == {}


def test_unreadable_ir_files_are_skipped_and_reported(out: Path) -> None:
    (out / "ir" / "bad.json").write_text("{")
    (out / "ir" / "list.json").write_text("[]")
    (out / "ir" / "odd.json").write_text(json.dumps({"source_system": 5, "confidence": ["x"]}))
    board = sb.gather([out], root=None, evidence=None, today="d")
    assert board["metrics"]["out/grafana/alerts_manual"] == 1
    assert any("bad.json" in w for w in board["warnings"])


def test_hostile_names_do_not_reach_markdown_raw(tmp_path: Path) -> None:
    evil = tmp_path / "x|`<script>\x1b[31m"
    _monitor(evil / "ir", "a", "exact", source="gr|afana\x1b[2J\n# h")
    board = sb.gather([evil], root=None, evidence=None, today="d")
    text = sb.render_markdown(board)
    assert "\x1b" not in text
    assert "<script>" not in text
    assert "| `" not in text.split("## Warnings")[0] or "`<" not in text


def test_a_symlinked_ir_folder_outside_is_not_followed(tmp_path: Path) -> None:
    secret = tmp_path / "elsewhere"
    _monitor(secret, "a", "exact")
    out = tmp_path / "out"
    out.mkdir()
    (out / "ir").symlink_to(secret)
    board = sb.gather([out], root=None, evidence=None, today="d")
    assert board["metrics"] == {}


def test_live_evidence_counts_verified_and_lists_unproven(tmp_path: Path) -> None:
    ev = tmp_path / "ev.json"
    ev.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "a",
                        "source": "grafana",
                        "claim": "c",
                        "status": "verified",
                        "date": "2026-11-10",
                        "method": "m",
                        "evidence": "docs/x.md",
                    },
                    {
                        "id": "b",
                        "source": "grafana",
                        "claim": "c2",
                        "status": "not_proven",
                        "date": "2026-11-10",
                        "method": "",
                        "evidence": "",
                    },
                    {"id": "c", "status": "verified"},
                ]
            }
        )
    )
    board = sb.gather([], root=None, evidence=ev, today="d")
    assert board["metrics"]["live/grafana/verified"] == 1
    assert [i["id"] for i in board["live"]["not_proven"]] == ["b"]
    assert any("c" in w for w in board["warnings"])  # incomplete entry is flagged, not counted


def test_tracked_evidence_file_is_valid() -> None:
    root = Path(__file__).resolve().parent.parent
    board = sb.gather(
        [], root=None, evidence=root / "docs/improvement/live-evidence.json", today="d"
    )
    assert board["warnings"] == []
    for item in board["live"]["verified"]:
        assert (root / item["evidence"].split("#")[0]).exists(), item["id"]


def test_tests_are_counted_statically(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_x(): pass\ndef test_y(): pass\n")
    board = sb.gather([], root=tmp_path, evidence=None, today="d")
    assert board["metrics"]["tests/test_functions"] == 2


def test_baseline_fixture_alerts_are_included(tmp_path: Path) -> None:
    imp = tmp_path / "docs" / "improvement"
    imp.mkdir(parents=True)
    (imp / "baseline.json").write_text(
        json.dumps(
            {
                "sources": {
                    "gcp": {
                        "converted": 2,
                        "total": 4,
                        "by_confidence": {"approximate": 2, "manual": 2},
                    }
                }
            }
        )
    )
    m = sb.gather([], root=tmp_path, evidence=None, today="d")["metrics"]
    assert m["fixtures/gcp/alerts_converted"] == 2
    assert m["fixtures/gcp/alerts_manual"] == 2


def test_remediation_top_five_comes_from_the_plan(out: Path) -> None:
    board = sb.gather([out], root=None, evidence=None, today="d")
    assert isinstance(board["remediation"], list)
    assert len(board["remediation"]) <= 5


# --- compare and stall wording -------------------------------------------------------------


def _board(**metrics: int) -> dict[str, object]:
    return {
        "measured": "2026-11-09",
        "metrics": {k.replace("__", "/"): v for k, v in metrics.items()},
    }


@pytest.mark.parametrize(
    ("name", "before", "after", "verdict"),
    [
        ("out/g/alerts_converted", 2, 3, "MOVED (better)"),
        ("out/g/alerts_converted", 3, 2, "MOVED (worse)"),
        ("out/g/alerts_manual", 3, 2, "MOVED (better)"),
        ("out/g/widgets_placeholders", 1, 5, "MOVED (worse)"),
        ("out/g/parity_mismatch", 1, 0, "MOVED (better)"),
        ("out/g/alerts_converted", 2, 2, "NOT MOVED"),
        ("tests/test_functions", 10, 20, "MOVED (info)"),
    ],
)
def test_compare_verdict_per_metric(name: str, before: int, after: int, verdict: str) -> None:
    deltas = sb.compare({"metrics": {name: before}}, {"metrics": {name: after}})
    assert deltas[0].verdict == verdict


def test_compare_new_and_gone_metrics() -> None:
    deltas = {
        d.metric: d.verdict
        for d in sb.compare(
            {"metrics": {"a/b/alerts_manual": 1}}, {"metrics": {"a/b/alerts_converted": 1}}
        )
    }
    assert deltas == {"a/b/alerts_manual": "GONE", "a/b/alerts_converted": "NEW"}


def test_stall_wording_when_only_info_moved() -> None:
    prev = {
        "measured": "2026-11-09",
        "metrics": {"o/g/alerts_converted": 2, "tests/test_functions": 5},
    }
    cur = {"metrics": {"o/g/alerts_converted": 2, "tests/test_functions": 50}}
    msg = sb.verdict_line(sb.compare(prev, cur), prev)
    assert msg == (
        "No outcome metric moved since 2026-11-09. Tests and other info changed, "
        "which is not progress. Propose a different lever."
    )


def test_progress_wording() -> None:
    prev = {"measured": "2026-11-09", "metrics": {"o/g/alerts_converted": 2}}
    cur = {"metrics": {"o/g/alerts_converted": 3}}
    assert (
        sb.verdict_line(sb.compare(prev, cur), prev)
        == "1 outcome metric(s) moved better, 0 worse, since 2026-11-09."
    )


def test_worse_is_called_worse() -> None:
    prev = {"measured": "2026-11-09", "metrics": {"o/g/alerts_converted": 3}}
    cur = {"metrics": {"o/g/alerts_converted": 1}}
    assert "worse" in sb.verdict_line(sb.compare(prev, cur), prev)


def test_history_roundtrip_and_stall(tmp_path: Path) -> None:
    hist = tmp_path / "h.csv"
    for date, conv in [("d1", 1), ("d2", 2), ("d3", 2), ("d4", 2), ("d5", 2)]:
        sb.append_history(
            hist, date, {"o/g/alerts_converted": conv, "tests/test_functions": int(date[1:]) * 9}
        )
    rows = sb.read_history(hist)
    assert len(rows) == 5
    assert sb.stalled_since(rows, 3) == "d2"
    assert sb.stalled_since(rows, 4) is None
    assert "since d2" in sb.stall_message(rows, 3)
    assert "different lever" in sb.stall_message(rows, 3)


def test_history_ignores_corrupt_lines(tmp_path: Path) -> None:
    hist = tmp_path / "h.csv"
    hist.write_text("date,metrics\nd1,a=1\ngarbage\nd2,a=x;b\n")
    assert [r.date for r in sb.read_history(hist)] == ["d1", "d2"]


# --- command --------------------------------------------------------------------------------


def test_command_writes_files_and_prints_compare(tmp_path: Path, out: Path) -> None:
    runner = CliRunner()
    dest = tmp_path / "board"
    args = [
        "scoreboard",
        "--in",
        str(out),
        "--root",
        str(tmp_path),
        "--out",
        str(dest),
        "--evidence",
        str(tmp_path / "none.json"),
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert (dest / "scoreboard.md").is_file()
    assert (dest / "scoreboard-history.csv").is_file()
    previous = tmp_path / "prev.json"
    previous.write_text((dest / "scoreboard.json").read_text())
    second = runner.invoke(app, [*args, "--compare", str(previous)])
    assert second.exit_code == 0, second.output
    assert "NOT MOVED" in second.output
    assert "No outcome metric moved since" in second.output
    assert len((dest / "scoreboard-history.csv").read_text().splitlines()) == 3


def test_command_no_write_leaves_disk_alone(tmp_path: Path, out: Path) -> None:
    dest = tmp_path / "board"
    res = CliRunner().invoke(
        app,
        ["scoreboard", "--in", str(out), "--out", str(dest), "--no-write", "--root", str(tmp_path)],
    )
    assert res.exit_code == 0, res.output
    assert not dest.exists()


def test_command_rejects_bad_compare_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    res = CliRunner().invoke(
        app, ["scoreboard", "--no-write", "--root", str(tmp_path), "--compare", str(bad)]
    )
    assert res.exit_code == 1
    assert "Error" in res.output


def test_ci_flag_without_gh_says_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert sb.ci_status() == "unknown (gh not installed)"


def test_ci_flag_parses_gh_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/gh")

    class Done:
        returncode = 0
        stdout = (
            '[{"status": "completed", "conclusion": "success", "headSha": "abcdef123",'
            ' "createdAt": "2026-11-10T10:00:00Z"}]'
        )

    monkeypatch.setattr("subprocess.run", lambda *_a, **_k: Done())
    assert sb.ci_status() == "success (abcdef1, 2026-11-10T10:00:00Z)"


def test_parity_list_form_is_accepted(out: Path) -> None:
    (out / "parity.json").write_text(json.dumps([{"verdict": "match"}, {"verdict": "mismatch"}]))
    m = sb.gather([out], root=None, evidence=None, today="d")["metrics"]
    assert m["out/grafana/parity_match"] == 1
    assert m["out/grafana/parity_mismatch"] == 1


def test_parity_source_key_names_the_scope(out: Path) -> None:
    (out / "parity.json").write_text(json.dumps({"source": "dynatrace", "verdicts": {"match": 2}}))
    m = sb.gather([out], root=None, evidence=None, today="d")["metrics"]
    assert m["out/dynatrace/parity_match"] == 2
