# Brand marks in reports

The HTML migration report shows the source product mark, an arrow and the New Relic mark as inline
SVG (no network access at view time; the file stays self-contained). Markdown reports get a text
heading and a text badge line only.

| Mark | File | Source |
|---|---|---|
| Grafana | `grafana.svg` | Simple Icons |
| Datadog | `datadog.svg` | Simple Icons |
| Dynatrace | `dynatrace.svg` | Simple Icons |
| Google Cloud | `googlecloud.svg` | Simple Icons |
| New Relic | `newrelic.svg` | Simple Icons |

- Project: https://github.com/simple-icons/simple-icons (files fetched from the `develop` branch
  on 2026-11-10, path `icons/<name>.svg`).
- Licence: the Simple Icons SVG files are CC0 1.0 Universal; the licence text is copied in
  `simple-icons-LICENSE.md`.
- The path data is embedded in `src/importer/core/brand_marks.py`; the SVG files here are the
  reference copies. Update both together.
- Not available in Simple Icons: AWS CloudWatch (and Microsoft Azure) were removed. AWS reports use
  a neutral text badge, never an imitation of a logo.
- The names and marks are trademarks of their owners. They are used only to identify the source
  or target product of a migration, with no claim of endorsement.
- Brand colours appear only as a thin accent border next to the name. The marks use the text
  colour, so contrast follows the existing light and dark themes, and they are `aria-hidden`
  (the strip carries `role="img"` and an `aria-label` such as "Grafana to New Relic").
