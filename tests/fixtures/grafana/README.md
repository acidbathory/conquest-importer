# Grafana fixtures

- `provisioning_alert_rules.json` and `export_alert_rules.json` are real read-backs (scrubbed) of the
  same 17 paused sample rules, taken from a Grafana Cloud sandbox stack (Grafana 13.4) through
  `GET /api/v1/provisioning/alert-rules` and `GET /api/v1/provisioning/alert-rules/export?format=json`.
- `ruler_rules.json` and `apis_alert_rules.json` are HAND-MADE. No real read-back of the ruler shape
  (`/api/ruler/grafana/api/v1/rules`) or of the `/apis` shape was saved. Their field names follow the
  research note (section 4a and Part A section 1.1), so they show what the normalisers expect, not
  what a live stack was seen to return. Replace them with scrubbed live read-backs when available.
- The provisioning and export files hold the same rules (same uids). The loader keeps the first
  copy of a uid, so the folder counts each rule once.
