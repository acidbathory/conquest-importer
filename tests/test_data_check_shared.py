"""One implementation of the data-check command body, for every source that has one."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from importer.core.apply import GuardError, NewRelicAPIError, NewRelicClient
from importer.core.data_check import Check, Status, run_data_check
from importer.core.ir import IRMonitor
from importer.core.secrets import MissingSecretError
from importer.sources import REGISTRY

SRC = Path(__file__).parent.parent / "src" / "importer"
IR = Path(__file__).parent / "fixtures" / "cloudwatch"


class Kit:
    """Stands in for a source module: check_all, render_report, has_blind_conditions."""

    def __init__(self, status: Status = Status.DATA, error: Exception | None = None) -> None:
        self.status, self.error, self.seen = status, error, []

    seen: list[tuple[int, int, int]]

    def check_all(
        self, client: NewRelicClient, account_id: int, monitors: list[IRMonitor], hours: int
    ) -> list[Check]:
        if self.error:
            raise self.error
        self.seen.append((account_id, len(monitors), hours))
        return [Check("c", self.status, 1, 1.0, "")]

    def render_report(self, checks: list[Check], hours: int) -> str:
        return f"report for {hours}h: " + ", ".join(c.status.value for c in checks)

    def has_blind_conditions(self, checks: list[Check]) -> bool:
        return any(c.status is Status.NO_DATA for c in checks)


@contextmanager
def opener(region: str) -> Iterator[NewRelicClient]:
    assert region == "eu"
    yield None  # type: ignore[misc]


def make_alerts(tmp_path: Path, count: int = 2) -> Path:
    (tmp_path / "ir").mkdir(parents=True)
    for monitor in REGISTRY["cloudwatch"].convert(IR)[:count]:
        (tmp_path / "ir" / f"{monitor.source_id}.json").write_text(monitor.model_dump_json())
    assert len(list((tmp_path / "ir").glob("*.json"))) == count
    return tmp_path


def run(
    alerts: Path,
    kit: Kit,
    *,
    account: int = 7,
    hours: int = 24,
    out: Path | None = None,
) -> tuple[int, list[str], list[str]]:
    said: list[str] = []
    errors: list[str] = []
    code = run_data_check(
        alerts, account, "eu", hours, out, kit, said.append, errors.append, open_client=opener
    )
    return code, said, errors


def test_data_arrives_exits_zero_prints_and_writes_the_report(tmp_path: Path) -> None:
    kit, target = Kit(), tmp_path / "r.md"
    code, said, errors = run(make_alerts(tmp_path / "a"), kit, out=target)
    assert (code, errors) == (0, [])
    assert said == ["report for 24h: data arrives"] and target.read_text() == said[0]
    assert kit.seen == [(7, 2, 24)]


def test_a_blind_condition_exits_two_after_the_report_is_written(tmp_path: Path) -> None:
    target = tmp_path / "r.md"
    code, said, _ = run(make_alerts(tmp_path / "a"), Kit(Status.NO_DATA), out=target)
    assert code == 2 and "no data" in said[0] and target.exists()


@pytest.mark.parametrize(("account", "hours"), [(0, 24), (7, 0), (7, 169), (-1, 24)])
def test_the_account_and_window_are_checked_before_anything_is_read(
    tmp_path: Path, account: int, hours: int
) -> None:
    kit = Kit()
    code, said, errors = run(tmp_path / "missing", kit, account=account, hours=hours)
    assert code == 1 and said == [] and "--nr-account-id" in errors[0] and kit.seen == []


@pytest.mark.parametrize("hours", [1, 168])
def test_the_window_bounds_are_inclusive(tmp_path: Path, hours: int) -> None:
    assert run(make_alerts(tmp_path / "a"), Kit(), hours=hours)[0] == 0


@pytest.mark.parametrize(
    "error",
    [
        NewRelicAPIError("boom"),
        GuardError("guard"),
        MissingSecretError("NEW_RELIC_API_KEY is not set"),
        ValueError("bad"),
        OSError("disk"),
    ],
)
def test_failures_exit_one_with_a_short_message(tmp_path: Path, error: Exception) -> None:
    code, said, errors = run(make_alerts(tmp_path / "a"), Kit(error=error))
    assert code == 1 and said == [] and errors == [f"Error: {error}"]


def test_a_folder_without_ir_checks_nothing(tmp_path: Path) -> None:
    kit = Kit()
    code, said, _ = run(tmp_path, kit)
    assert code == 0 and said == ["report for 24h: data arrives"] and kit.seen == [(7, 0, 24)]


def test_the_message_is_cut_and_has_no_control_characters(tmp_path: Path) -> None:
    code, _, errors = run(
        make_alerts(tmp_path / "a"), Kit(error=ValueError("\x1b[31m" + "x" * 999))
    )
    assert code == 1 and "\x1b" not in errors[0] and len(errors[0]) < 400


def test_the_body_exists_once_in_the_source_tree() -> None:
    hits = [
        p.relative_to(SRC).as_posix()
        for p in sorted(SRC.rglob("*.py"))
        if "--hours must be 1 to 168" in p.read_text()
    ]
    assert hits == ["core/data_check.py"]
