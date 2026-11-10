"""Read a range of a Prometheus (or Loki) query, read-only.

Two ways in: straight to a Prometheus URL (such as http://127.0.0.1:9090), or through Grafana's
data source proxy with GRAFANA_URL and GRAFANA_SA_TOKEN. Only GET calls on a fixed list of paths
run; the host is checked before anything is sent (Grafana: a Cloud stack or the host named by
--allow-host; a direct URL: loopback, or the https host named by --allow-host); redirects are not
followed; the safeguards of `core/safe_http.py` apply (token scrubbing, byte caps per response
and per run, deadline). On top of those: a cap on requests, on series and on points.
"""

import math
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from importer.core.safe_http import ReadBudget, get_json
from importer.core.text import printable
from importer.parity.model import Fetched, ParityError, Series
from importer.sources.grafana.extract import GrafanaError, api_token, grafana_url, validate_host

SERVICE = "the data source"
PROM_PATH = "/api/v1/query_range"
LOKI_PATH = "/loki/api/v1/query_range"
UID = r"[A-Za-z0-9_-]{1,40}"
NAME = r"[A-Za-z0-9 _.-]{1,100}"
PROXY = re.compile(rf"/api/datasources/proxy/uid/{UID}(?:{PROM_PATH}|{LOKI_PATH})")
LOOKUP = re.compile(rf"/api/datasources/(?:uid/{UID}|name/{NAME})")
DIRECT = re.compile(rf"(?:/[A-Za-z0-9_-]{{1,40}}){{0,3}}(?:{PROM_PATH}|{LOKI_PATH})")
NUMBER = re.compile(r"\d{1,12}(?:\.\d{1,3})?")
PARAMS = {
    "query": re.compile(r"[^\x00-\x1f\x7f]{1,4000}"),
    "start": NUMBER,
    "end": NUMBER,
    "step": re.compile(r"\d{1,6}"),
    "limit": re.compile(r"\d{1,5}"),
}
MAX_REQUESTS = 1000
MAX_SERIES = 200
MAX_POINTS = 11_000  # Prometheus itself refuses more per series
MAX_TOTAL_POINTS = 400_000
MAX_LABEL = 200
LOOPBACK = re.compile(r"localhost|::1|127(?:\.\d{1,3}){3}")
Fetch = Callable[[str, str, int, int, int], Fetched]  # family, query, start, end, step


class SourceError(ParityError):
    pass


def allowed(path: str, params: dict[str, str]) -> bool:
    """True for a query_range call, a data source lookup, and nothing else."""
    fixed = PROXY.fullmatch(path) or LOOKUP.fullmatch(path) or DIRECT.fullmatch(path)
    return bool(fixed) and all(k in PARAMS and PARAMS[k].fullmatch(v) for k, v in params.items())


def check_direct(url: str, allow_host: str | None) -> str:
    """The base URL of a Prometheus given directly; loopback, or https plus --allow-host."""
    if not url.isascii():
        raise SourceError("the Prometheus URL must be plain ASCII")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise SourceError("the Prometheus URL is not valid") from None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise SourceError("the Prometheus URL must look like http://127.0.0.1:9090")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise SourceError("the Prometheus URL must not hold a user, a query or a fragment")
    if not re.fullmatch(r"(?:/[A-Za-z0-9_-]{1,40}){0,3}/?", parts.path or ""):
        raise SourceError("the Prometheus URL path may only be a short prefix such as /prometheus")
    named = allow_host is not None and allow_host.strip().lower() == host
    if not LOOPBACK.fullmatch(host) and not (parts.scheme == "https" and named):
        raise SourceError(
            "a Prometheus that is not on this machine needs https and --allow-host <its host>"
        )
    shown = f"[{host}]" if ":" in host else host
    return (
        f"{parts.scheme}://{shown}" + (f":{port}" if port else "") + (parts.path or "").rstrip("/")
    )


def _finite(text: object) -> float | None:
    try:
        value = float(str(text))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse_matrix(body: dict[str, Any]) -> Fetched:
    """Series from a query_range answer; caps on series and points mark the answer truncated."""
    if body.get("status") != "success":
        raise SourceError("the data source did not report success")
    data = body.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, list) or (
        isinstance(data, dict) and data.get("resultType") != "matrix"
    ):
        raise SourceError("the data source did not return a matrix (a range of values)")
    out: list[Series] = []
    truncated = len(result) > MAX_SERIES
    total = 0
    for entry in result[:MAX_SERIES]:
        if not isinstance(entry, dict) or not isinstance(entry.get("values"), list):
            continue
        metric = entry.get("metric")
        labels = {
            printable(str(k), MAX_LABEL): printable(str(v), MAX_LABEL)
            for k, v in (metric.items() if isinstance(metric, dict) else [])
        }
        points: dict[int, float] = {}
        values = entry["values"]
        if len(values) > MAX_POINTS or total + len(values) > MAX_TOTAL_POINTS:
            truncated = True
            values = values[: max(0, min(MAX_POINTS, MAX_TOTAL_POINTS - total))]
        total += len(values)
        for pair in values:
            if isinstance(pair, list) and len(pair) == 2:
                t, v = _finite(pair[0]), _finite(pair[1])
                if t is not None and v is not None:
                    points[round(t)] = v
        out.append(Series(labels, points))
    return Fetched(out, truncated)


class RangeReader:
    """Runs query_range calls against one data source under the request and byte budgets."""

    def __init__(
        self,
        client: httpx.Client,
        base: str,
        prefix: str,
        token: str,
        budget: ReadBudget | None = None,
    ) -> None:
        self._client, self._base, self._prefix, self._token = client, base, prefix, token
        self._budget = budget or ReadBudget()
        self.requests = 0

    def get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not allowed(path, params):
            raise SourceError("this path is not on the allow-list of read-only calls")
        self.requests += 1
        if self.requests > MAX_REQUESTS:
            raise SourceError(f"more than {MAX_REQUESTS} requests; stopped")
        encoded = quote(path, safe="/")
        try:
            return get_json(
                self._client,
                self._base + encoded,
                params,
                self._token,
                SourceError,
                SERVICE,
                self._budget,
            )
        except SourceError as exc:
            raise SourceError(printable(str(exc), 300)) from None

    def __call__(self, family: str, query: str, start: int, end: int, step: int) -> Fetched:
        path = self._prefix + (LOKI_PATH if family == "loki" else PROM_PATH)
        params = {"query": query, "start": str(start), "end": str(end), "step": str(step)}
        if family == "loki":
            params["limit"] = "5000"
        return parse_matrix(self.get(path, params))


def _resolve(get: Callable[[str, dict[str, str]], dict[str, Any]], ref: str) -> str:
    """The uid of the data source named or identified by `ref`."""
    for path in (f"/api/datasources/uid/{ref}", f"/api/datasources/name/{ref}"):
        if not LOOKUP.fullmatch(path):
            continue
        try:
            found = get(path, {})
        except SourceError as exc:
            if " 404" in f" {exc}":
                continue
            raise
        uid = found.get("uid")
        if isinstance(uid, str) and re.fullmatch(UID, uid):
            return uid
    raise SourceError(f"no data source with the uid or name {printable(ref, 60)} in Grafana")


@contextmanager
def open_reader(
    spec: str,
    *,
    allow_host: str | None = None,
    transport: httpx.BaseTransport | None = None,
    token: str | None = None,
    grafana: str | None = None,
) -> Iterator[RangeReader]:
    """A reader for a Prometheus URL or a Grafana data source (uid or name); closes on exit."""
    direct = spec.strip().lower().startswith(("http://", "https://"))
    if direct:
        base, prefix, secret, headers = check_direct(spec, allow_host), "", "", {}
    else:
        try:
            secret = token if token is not None else api_token()
            base = validate_host(grafana if grafana is not None else grafana_url(), allow_host)
        except (GrafanaError, ValueError) as exc:
            raise SourceError(printable(str(exc), 300)) from None
        prefix, headers = "", {"Authorization": f"Bearer {secret}"}
    headers["Accept"] = "application/json"
    with httpx.Client(
        timeout=60, headers=headers, transport=transport, follow_redirects=False
    ) as client:
        reader = RangeReader(client, base, prefix, secret)
        if not direct:
            reader._prefix = f"/api/datasources/proxy/uid/{_resolve(reader.get, spec.strip())}"
        yield reader
