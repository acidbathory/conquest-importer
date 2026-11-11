"""The cookbook as an HTML page: relevant-only after a run, everything standalone."""

import re
from html import escape
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core import cookbook
from importer.core.cookbook_html import found_in, render_cookbook_html
from importer.core.remediation_plan import Item, Plan, build_plan, read_items

FIX = Path(__file__).parent / "fixtures"
runner = CliRunner()
HOSTILE = "<img src=x onerror=alert(1)>\"&'`"


@pytest.fixture(scope="module")
def converted(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("cb")
    for args in (
        ["convert", "--source", "grafana", "--in", str(FIX / "grafana"), "--out", str(root / "a")],
        [
            "convert-grafana-dashboards",
            "--in",
            str(FIX / "grafana-dashboard-convert"),
            "--out",
            str(root / "d"),
        ],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
    return root


@pytest.fixture(scope="module")
def plan(converted: Path) -> Plan:
    return build_plan(read_items(converted))


def _hostile(plan: Plan) -> Plan:
    donor = next(i for i in plan.items if i.cookbook is not None)
    bad = Item(donor.source, "alert rule", "", HOSTILE, donor.reasons, 1, donor.variant, HOSTILE)
    return build_plan([*plan.items, bad])


def test_relevant_page_holds_only_blockers_the_run_hit_with_plan_counts(plan: Plan) -> None:
    hits, other = found_in(plan)
    html = render_cookbook_html(plan)
    assert hits and sum(h.count for h in hits) + other == sum(i.count for i in plan.items)
    for h in hits:
        assert f'id="{h.blocker.key}"' in html and f"{h.count} in your run" in html
        for title, n in h.groups.items():
            group = next(g for g in plan.groups if g.title == title)
            assert n <= group.count
    assert [h.count for h in hits] == sorted((h.count for h in hits), reverse=True)
    absent = [b for b in cookbook.blockers() if b.key not in {h.blocker.key for h in hits}]
    assert absent
    for b in absent:
        assert f'id="{b.key}"' not in html and b.title not in html
    assert "Found in your migration" in html
    assert html.count("<details") == len(hits) == html.count('<details class="blocker"')
    assert html.count(" open>") == len(hits)


def test_single_group_count_equals_the_group_count(plan: Plan) -> None:
    hits, _ = found_in(plan)
    for g in plan.groups:
        blockers = {i.cookbook.key for i in g.items if i.cookbook}
        if len(blockers) == 1 and all(i.cookbook for i in g.items):
            hit = next(h for h in hits if h.blocker.key in blockers)
            assert hit.groups[g.title] == g.count


def test_every_method_status_and_recommended_marker_appear(plan: Plan) -> None:
    hits, _ = found_in(plan)
    html = render_cookbook_html(plan)
    for h in hits:
        for m in h.blocker.methods:
            assert escape(m.name) in html
            assert m.status in html
    assert html.count("Recommended first") == len(hits)
    for s in cookbook.STATUSES:
        assert f"{s}</span> " in html  # the legend, label text and not colour alone


def test_hostile_names_and_queries_are_escaped(plan: Plan) -> None:
    html = render_cookbook_html(_hostile(plan), plan_path='x"><script>.md')
    assert "<img" not in html and "<script" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert 'x"><script>' not in html


def test_code_blocks_keep_their_text_and_line_breaks() -> None:
    html = render_cookbook_html()
    total = 0
    for b in cookbook.blockers():
        for m in b.methods:
            for ex in m.examples:
                total += 1
                lines = [x for x in ex.text.split("\n") if x.strip()]
                body = re.escape(
                    ex.text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace('"', "&quot;")
                    .replace("'", "&#x27;")
                    .replace("\t", "  ")
                    .split("\n")[0]
                )
                assert re.search(body, html), lines[0]
    assert total and html.count('<pre class="code"') == total
    assert any("\n" in x for x in re.findall(r'<pre class="code"[^>]*>(.*?)</pre>', html, re.S))


def test_full_page_has_every_blocker_every_method_and_a_contents_list() -> None:
    html = render_cookbook_html()
    assert html.count('class="method') >= sum(len(b.methods) for b in cookbook.blockers())
    for b in cookbook.blockers():
        assert f'href="#{b.key}"' in html and f'id="{b.key}"' in html
    assert " open>" not in html and "Found in your migration" not in html


def test_links_are_https_only_and_no_script_or_external_asset() -> None:
    html = render_cookbook_html()
    hrefs = re.findall(r'href="([^"]*)"', html)
    assert hrefs
    for h in hrefs:
        assert h.startswith(("https://", "#")), h
    assert html.count('rel="noopener noreferrer"') == len([h for h in hrefs if h[0] != "#"])
    assert "<script" not in html and "src=" not in html and "@import" not in html
    assert "default-src 'none'" in html


def test_print_stylesheet_prints_on_white_with_unbroken_code() -> None:
    html = render_cookbook_html()
    printed = html.split("@media print")[-1]
    assert "--paper:#fff" in printed and "background:#fff" in printed
    assert "break-inside:avoid" in printed and "pre.code" in printed


def test_masthead_and_brand_match_the_report(plan: Plan) -> None:
    html = render_cookbook_html(plan, source="grafana")
    assert "Grafana to New Relic" in html and 'class="masthead"' in html and "<svg" in html


def test_empty_run_says_so_in_one_short_page() -> None:
    html = render_cookbook_html(Plan([], [], []))
    assert "Nothing in this run needs a cookbook method." in html
    assert "<details" not in html and "Decision guide" not in html
    assert len(html) < 40_000


def test_guide_is_trimmed_when_no_alert_is_involved(plan: Plan) -> None:
    dash = Plan(
        [i for i in plan.items if "alert" not in i.kind],
        [],
        [],
    )
    items = [i for i in plan.items if i.kind == "dashboard panel" and i.cookbook]
    html = render_cookbook_html(build_plan(items))
    assert dash and "<b>Is it an alert?</b>" not in html and "<b>How much delay" not in html
    with_alerts = render_cookbook_html(plan)
    assert "<b>Is it an alert?</b>" in with_alerts


def test_command_standalone_writes_the_full_page(tmp_path: Path, help_text: object) -> None:
    out = tmp_path / "x" / "cookbook.html"
    result = runner.invoke(app, ["cookbook", "--out", str(out)])
    assert result.exit_code == 0, result.output
    text = out.read_text()
    assert all(f'id="{b.key}"' in text for b in cookbook.blockers())
    assert "Found in your migration" not in text


def test_command_with_in_is_relevant_only_and_all_overrides(
    tmp_path: Path, converted: Path
) -> None:
    out = tmp_path / "c.html"
    assert (
        runner.invoke(app, ["cookbook", "--in", str(converted), "--out", str(out)]).exit_code == 0
    )
    some = out.read_text()
    assert "Found in your migration" in some and some.count("<details") < len(cookbook.blockers())
    full = tmp_path / "all.html"
    args = ["cookbook", "--in", str(converted), "--all", "--out", str(full)]
    assert runner.invoke(app, args).exit_code == 0
    assert full.read_text().count('<details class="blocker"') == len(cookbook.blockers())


def test_command_rejects_bad_input(tmp_path: Path) -> None:
    bad = runner.invoke(
        app, ["cookbook", "--in", str(tmp_path / "nope"), "--out", str(tmp_path / "o")]
    )
    assert bad.exit_code == 1
    bad = runner.invoke(app, ["cookbook", "--source", "zzz", "--out", str(tmp_path / "o")])
    assert bad.exit_code == 1


def test_command_help_lists_the_flags(help_text: "object") -> None:
    text = help_text("cookbook", "--help")  # type: ignore[operator]
    for flag in ("--in", "--all", "--out", "--source"):
        assert flag in text
