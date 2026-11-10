"""Plain data shared by the parity modules."""

from dataclasses import dataclass, field
from enum import StrEnum


class Verdict(StrEnum):
    VERIFIED = "VERIFIED"
    CLOSE = "CLOSE"
    MISMATCH = "MISMATCH"
    NO_DATA = "NO DATA"
    NOT_COMPARABLE = "NOT COMPARABLE"


class ParityError(Exception):
    """A read or a setting failed; the message is safe to show."""


@dataclass
class Series:
    """One labelled series. `points` maps a whole-second timestamp to a finite value."""

    labels: dict[str, str]
    points: dict[int, float] = field(default_factory=dict)


@dataclass
class Fetched:
    series: list[Series]
    truncated: bool = False  # a cap on series or points cut the answer short


@dataclass(frozen=True)
class Pair:
    """An original query and the NRQL it was converted to."""

    kind: str  # tile | alert
    owner: str  # dashboard or alert name
    owner_key: str  # the source's own key (dashboard uid or rule uid)
    title: str  # widget title, or the alert name
    family: str  # prometheus | loki
    original: str
    nrql: str
    timeseries: bool = True
    variables: tuple[tuple[str, tuple[str, ...]], ...] = ()
    skip: str = ""  # a reason this pair cannot be compared at all


@dataclass
class SeriesResult:
    labels: dict[str, str]
    points: int
    within: int
    median_error: float | None


@dataclass
class Outcome:
    verdict: Verdict
    cause: str = ""
    points: int = 0
    within: int = 0
    median_error: float | None = None
    series: list[SeriesResult] = field(default_factory=list)
    source_only: int = 0
    nr_only: int = 0
    note: str = ""

    @property
    def fraction(self) -> float | None:
        return self.within / self.points if self.points else None


@dataclass
class Item:
    pair: Pair
    outcome: Outcome
    original_sent: str = ""  # the PromQL after variables were filled in
    nrql_sent: str = ""
