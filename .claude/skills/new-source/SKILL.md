---
name: new-source
description: Plan and scaffold a new source platform (Azure Monitor, Datadog, Prometheus rules and so on) the way the AWS and Google sources were built. Use when asked to add support for another platform.
---

Build a new source in small, reviewable steps. Read `docs/dev/adding-a-source.md` and the
existing `src/importer/sources/gcp/` package first; it is the smallest complete example.

1. Research (`researcher` subagent): the platform's alert object model, query language, export
   API, how its metrics reach New Relic, and what New Relic documents collecting. Save the note
   in `docs/research/<platform>-native-coverage.md` with evidence levels.
2. Models: pydantic models for the export format, loaded by `load_*`. Keep unknown fields.
3. Translate: one IR monitor per condition. Anything not understood is `Confidence.MANUAL` with
   a reason. Never guess a metric name; unverified names carry the "not confirmed" note.
4. Remediation rules for every manual reason, registered with `register_rules`. Add taxonomy
   keywords for new reason wording.
5. Register the source in `sources/__init__.py` and add a scrubbed fixture under
   `tests/fixtures/<platform>/` (no real ids, emails or hosts).
6. Extractor (read-only, allow-listed calls, no secrets written) and the link/ingest generator
   if data does not already reach New Relic.
7. Add the fixture folder to `FIXTURES` in `src/importer/core/improve.py` and record a new
   baseline with the owner's approval.
8. Live verification of metric names in a sandbox, with cost named first. The owner approves
   anything that creates cloud resources or grants IAM.
9. Update `docs/what-migrates.md`, the README roadmap and the report profile.
