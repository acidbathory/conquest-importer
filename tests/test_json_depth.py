"""The depth check runs before parsing, so the result does not depend on the Python version."""

import time
from pathlib import Path

import httpx
import pytest

from importer.core import safe_http
from importer.core.json_depth import MAX_JSON_DEPTH, TooDeepError, check_depth

DEEP = "[" * 100_000
DEEP_CLOSED = "[" * 100_000 + "]" * 100_000


def test_depth_exactly_at_the_limit_passes() -> None:
    check_depth("[" * 5 + "]" * 5, 5)
    check_depth("[" * MAX_JSON_DEPTH + "]" * MAX_JSON_DEPTH)


def test_one_level_over_the_limit_fails() -> None:
    with pytest.raises(TooDeepError, match="nested too deeply"):
        check_depth("[" * 6 + "]" * 6, 5)


def test_an_unclosed_run_of_brackets_fails() -> None:
    with pytest.raises(ValueError, match="nested too deeply"):
        check_depth(DEEP)


def test_brackets_inside_strings_are_ignored() -> None:
    check_depth('{"a": "' + "[" * 1000 + '"}', 3)


def test_escaped_quotes_do_not_end_a_string() -> None:
    check_depth('["\\"' + "[" * 1000 + '"]', 3)
    # an escaped backslash does not escape the quote after it, so the string ends there
    with pytest.raises(TooDeepError):
        check_depth('["\\\\", ' + "[" * 10 + "]]", 3)


def test_mixed_arrays_and_objects_count_together() -> None:
    check_depth('{"a":[{"b":[1]}]}', 4)
    with pytest.raises(TooDeepError):
        check_depth('{"a":[{"b":[1]}]}', 3)


def test_siblings_do_not_add_up() -> None:
    check_depth("[" + "[]," * 10_000 + "[]]", 2)


def test_bytes_work_like_text() -> None:
    check_depth(b"[[1]]", 2)
    with pytest.raises(TooDeepError):
        check_depth(b"[[[1]]]", 2)
    check_depth('["é€"]'.encode(), 1)


def test_a_megabyte_is_scanned_in_linear_time() -> None:
    start = time.process_time()
    check_depth('{"k": [1, 2, "x"]},' * 55_000)
    check_depth(b"[" * 100 + b" " * 1_000_000)
    assert time.process_time() - start < 10


def test_the_error_is_a_value_error_with_the_limit() -> None:
    with pytest.raises(ValueError, match="more than 200 levels"):
        check_depth(DEEP)


# Every entry point that reads untrusted JSON refuses a 100,000-deep body with a clean error.


def _http(body: str) -> httpx.Response:
    return httpx.Response(200, content=body.encode(), headers={"content-type": "application/json"})


class _Boom(Exception):
    pass


@pytest.mark.parametrize("body", [DEEP, DEEP_CLOSED], ids=["unclosed", "closed"])
def test_safe_http_decode_json(body: str) -> None:
    with pytest.raises(_Boom, match="nested too deeply"):
        safe_http.decode_json(_http(body), "tok", _Boom, "Svc")


@pytest.mark.parametrize("body", [DEEP, DEEP_CLOSED], ids=["unclosed", "closed"])
def test_the_http_decoders_do_not_crash(body: str) -> None:
    from importer.sources.dynatrace import extract as dt
    from importer.sources.grafana import extract as gf
    from importer.sources.grafana.dashboards import extract as gd
    from importer.sources.grafana.notifications import extract as gn

    with pytest.raises(dt.DynatraceError):
        dt._decode(_http(body), "tok")
    with pytest.raises(gf.GrafanaError):
        gf._decode(_http(body), "tok")
    with pytest.raises(gf.GrafanaError, match="not a JSON object or list"):
        gd._decode(_http(body), "tok", safe_http.ReadBudget())
    with pytest.raises(gf.GrafanaError, match="nested too deeply"):
        gn._decode(_http(body), "tok", safe_http.ReadBudget())


@pytest.mark.parametrize("body", [DEEP, DEEP_CLOSED], ids=["unclosed", "closed"])
def test_the_file_loaders_refuse_deep_nesting(tmp_path: Path, body: str) -> None:
    from importer.sources.dynatrace import models as dt_models
    from importer.sources.dynatrace.classic import models as classic
    from importer.sources.dynatrace.dashboards import models as dash
    from importer.sources.dynatrace.inventory import models as inv
    from importer.sources.grafana import models as gf_models
    from importer.sources.grafana.dashboards import load as gf_dash
    from importer.sources.grafana.notifications import loader, rulefiles

    path = tmp_path / "deep.json"
    path.write_text(body)
    with pytest.raises(ValueError, match="nested"):
        classic.load_objects(path)
    with pytest.raises(ValueError, match="nested"):
        inv.read_json(path)
    with pytest.raises(ValueError, match="nested"):
        gf_models.load_rules(path)
    with pytest.raises(ValueError, match="nested"):
        rulefiles.load_rule_file(path)
    with pytest.raises(Exception, match="nested"):
        loader._read_json(path)
    with pytest.raises(ValueError, match="nested"):
        dt_models.load_exports(path)
    assert [d.error for d in dash.read_file(path)] == [dash.TOO_DEEP]
    assert gf_dash._read_json(path)[0] is None
