# 2026-11-11 gauge-rate

Pinned output changes: none. No fixture in tests/fixtures types a metric as a gauge under
rate, irate or increase, so grafana-dashboards, grafana-dashboard-convert and the alert fixtures
convert the same widgets and rules as before (the gate measure is unchanged).
