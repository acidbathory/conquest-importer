# 2026-11-11 gauge-rate

- The dashboard converter already refused a rate of a metric hinted as a gauge (through the shared
  `is_counter`), but with the reason "no counter suffix", which sends a person to relabel a metric
  that is a gauge. The alert path had a real hole: the older counter form checked only the name
  suffix, so `sum(rate(x_total[5m]))` converted even when the hints file typed `x_total` as a gauge.
- Both paths now use one question, `NameMap.is_gauge` (`hinted_gauge` for the active map), and
  refuse with their own reason (`R_RATE_OF_GAUGE`, `C_RATE_OF_GAUGE`) and a rule that points to
  derivative or latest-minus-earliest, or to correcting the hint if the metric is a counter.
- Only an explicit gauge hint triggers it; a name without a hint keeps the suffix guess.
- No fixture hints a gauge, so no widget or rule count and no pinned output changed.
