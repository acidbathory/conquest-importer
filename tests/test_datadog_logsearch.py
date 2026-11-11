"""D1: the log search tokenizer and parser (docs/research/datadog-verified.md section 5)."""

from importer.sources.datadog.logsearch import (
    LAnd,
    LNot,
    LOr,
    LTerm,
    parse_log_search,
)


def _ok(text: str):  # type: ignore[no-untyped-def]
    r = parse_log_search(text)
    assert r.ok, r.diagnostics
    return r


def test_implicit_and_of_attribute_and_tag_terms() -> None:
    r = _ok("source:resend service:events @type:email.bounced")
    assert r.lossless
    assert r.node == LAnd(
        (
            LTerm("source", "eq", "resend"),
            LTerm("service", "eq", "events"),
            LTerm("@type", "eq", "email.bounced"),
        )
    )


def test_exclusion_prefix_and_exists() -> None:
    r = _ok("a:b -@data.broadcast_id:*")
    assert r.node == LAnd((LTerm("a", "eq", "b"), LNot(LTerm("@data.broadcast_id", "exists", "*"))))
    assert _ok("@x:*").node == LTerm("@x", "exists", "*")


def test_comparison_and_range() -> None:
    assert _ok("@Severity:>=40").node == LTerm("@Severity", "cmp", "40", op=">=")
    assert _ok("@d:<5").node == LTerm("@d", "cmp", "5", op="<")
    r = _ok("@duration:[100 TO 200]")
    assert r.node == LTerm("@duration", "range", "100", op="[]", hi="200")


def test_or_and_parentheses_and_precedence() -> None:
    r = _ok("a:1 OR b:2 c:3")
    assert r.node == LOr(
        (LTerm("a", "eq", "1"), LAnd((LTerm("b", "eq", "2"), LTerm("c", "eq", "3"))))
    )
    r2 = _ok("(a:1 OR a:2) AND NOT b:3")
    assert r2.node == LAnd(
        (LOr((LTerm("a", "eq", "1"), LTerm("a", "eq", "2"))), LNot(LTerm("b", "eq", "3")))
    )


def test_value_group_expands_to_or_of_terms() -> None:
    assert _ok("status:(error OR warn)").node == LOr(
        (LTerm("status", "eq", "error"), LTerm("status", "eq", "warn"))
    )


def test_quoted_phrase_and_free_text() -> None:
    assert _ok('@msg:"hello world"').node == LTerm("@msg", "eq", "hello world", phrase=True)
    assert _ok("timeout").node == LTerm(None, "text", "timeout")
    assert _ok('"disk full"').node == LTerm(None, "text", "disk full", phrase=True)


def test_wildcards_and_escapes() -> None:
    t = _ok("host:web-*").node
    assert isinstance(t, LTerm) and t.wildcard
    assert _ok(r"@a:b\ c").node == LTerm("@a", "eq", "b c")
    assert _ok(r"@a:b\*").node == LTerm("@a", "eq", "b*")  # an escaped star is not a wildcard


def test_boolean_operators_are_case_sensitive() -> None:
    r = _ok("a:1 or b:2")  # lower case: three implicit-AND terms
    assert isinstance(r.node, LAnd) and len(r.node.items) == 3


def test_unbalanced_and_empty_input_do_not_raise() -> None:
    for text in ("(", ")", "a:1 AND", '"unclosed', "a:[1 TO", "-", "NOT", "", "   "):
        r = parse_log_search(text)
        assert isinstance(r.ok, bool)


def test_empty_search_is_ok_and_matches_everything() -> None:
    r = parse_log_search("*")
    assert r.ok and r.node is None


def test_caps() -> None:
    assert not parse_log_search("a:1 " * 5000).ok
    deep = "(" * 200 + "a:1" + ")" * 200
    r = parse_log_search(deep)
    assert not r.ok and r.diagnostics[0].code == "too_deep"


def test_all_real_log_searches_parse() -> None:
    import json
    from pathlib import Path

    from importer.sources.datadog.query import parse_chain

    root = Path(__file__).parent / "fixtures" / "datadog" / "real" / "monitors"
    seen = 0
    for path in sorted(root.glob("*.json")):
        data = json.loads(path.read_text())
        m = data.get("definition", data)
        if m["type"] == "log alert" and m["query"].startswith("logs("):
            chain = parse_chain(m["query"])
            assert chain.ok
            r = parse_log_search(str(chain.head_args[0]))
            assert r.ok and r.lossless, (path.name, r.diagnostics)
            seen += 1
    assert seen == 7
