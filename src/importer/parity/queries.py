"""Fill in dashboard variables and turn a converted NRQL into a timeseries over the window.

Variables come from the dashboard defaults or `--var name=value`. A variable with no single
default (an "all" value, a query variable) must be given with `--var`; otherwise the item is
reported as not comparable instead of guessing. Values reach a query only after a character
check, and NRQL values only as quoted literals.
"""

import re

from importer.core.emit.nrql import quote_string
from importer.parity.model import ParityError

SAFE_VALUE = re.compile(r"[^\x00-\x1f\x7f\"'`\\{}$]{0,200}")
NRQL_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
PROM_VARIABLE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::[a-z]+)?\}|\[\[([A-Za-z_][A-Za-z0-9_]*)\]\]"
    r"|\$([A-Za-z_][A-Za-z0-9_]*)"
)
TIMESERIES = re.compile(r"\s+TIMESERIES\s+(?:AUTO|MAX|\d+\s+[A-Za-z]+)\s*$", re.IGNORECASE)
HAS_TIMESERIES = re.compile(r"\bTIMESERIES\b", re.IGNORECASE)
FACET = re.compile(
    r"\bFACET\s+(.+?)(?=\s+(?:LIMIT|TIMESERIES|SINCE|UNTIL|ORDER|WITH|COMPARE)\b|\s*$)",
    re.IGNORECASE,
)
RANGE_WINDOW = re.compile(r"\[(\d+)([smhdw])\]")
WINDOW_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class Unfilled(ParityError):
    """A variable has no usable value."""


def check_values(name: str, values: tuple[str, ...]) -> None:
    for value in values:
        if value in ("*", "$__all") or not SAFE_VALUE.fullmatch(value):
            raise Unfilled(
                f"variable {name} has no single safe default; give one with --var {name}=<value>"
            )


def fill_promql(expr: str, variables: dict[str, tuple[str, ...]], step: int, hours: float) -> str:
    builtin = {
        "__rate_interval": f"{max(step, 1)}s",
        "__interval": f"{step}s",
        "__interval_ms": str(step * 1000),
        "__range": f"{int(hours * 3600)}s",
        "__range_s": str(int(hours * 3600)),
    }

    def one(match: re.Match[str]) -> str:
        name = next(g for g in match.groups() if g)
        if name in builtin:
            return builtin[name]
        values = variables.get(name)
        if not values:
            raise Unfilled(f"variable {name} has no value; give one with --var {name}=<value>")
        check_values(name, values)
        return "|".join(re.escape(v) if len(values) > 1 else v for v in values)

    return PROM_VARIABLE.sub(one, expr)


def fill_nrql(nrql: str, variables: dict[str, tuple[str, ...]]) -> str:
    def one(match: re.Match[str]) -> str:
        name = match.group(1)
        values = variables.get(name)
        if not values:
            raise Unfilled(f"variable {name} has no value; give one with --var {name}=<value>")
        check_values(name, values)
        return ", ".join(quote_string(v) for v in values)

    return NRQL_VARIABLE.sub(one, nrql)


def has_timeseries(nrql: str) -> bool:
    return bool(HAS_TIMESERIES.search(nrql))


def timeseries_over(nrql: str, start: int, end: int, step: int) -> str:
    """The NRQL with its bucket replaced by `step` seconds and the window fixed."""
    text = nrql.strip()
    if not re.match(r"(?:SELECT|FROM)\b", text, re.IGNORECASE) or ";" in text:
        raise ParityError("the converted query is not a single SELECT")
    text = TIMESERIES.sub("", text)
    if HAS_TIMESERIES.search(text):
        raise ParityError("the converted query has a TIMESERIES form this check cannot replace")
    return f"{text} TIMESERIES {step} SECONDS SINCE {start} UNTIL {end}"


def facet_names(nrql: str) -> list[str]:
    """Attribute names in the FACET clause (backticks removed); empty when there is none."""
    found = FACET.search(nrql)
    if not found:
        return []
    names: list[str] = []
    depth, current = 0, ""
    for ch in found.group(1):
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            names.append(current.strip().strip("`"))
            current = ""
        else:
            current += ch
    names.append(current.strip().strip("`"))
    return [n for n in names if n]


def longest_window(expr: str) -> int | None:
    """The longest `[5m]` range in a PromQL expression, in seconds."""
    spans = [int(n) * WINDOW_SECONDS[u] for n, u in RANGE_WINDOW.findall(expr)]
    return max(spans) if spans else None
