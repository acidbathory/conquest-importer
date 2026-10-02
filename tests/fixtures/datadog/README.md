# Datadog fixtures (synthetic)

Hand-written from Datadog's documented monitor format by `scripts/generate_fixtures.py`.
They have **not** been compared with a live Datadog account. Replace or extend them with real
`importer extract` output once access exists (check it contains nothing sensitive first).

- 9000001-9000020: the 20 planned sample monitors (easy cases through composite/anomaly/forecast)
- 9000021-9000030: edge cases: hostile text, a quote in a scope, unknown types, a window that
  New Relic cannot take, dropped settings (priority, renotify), SLO alert

Expected conversion results are recorded in `tests/golden/expected.json`.
