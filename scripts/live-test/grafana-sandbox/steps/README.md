# Grafana -> New Relic, step by step

Run from the repository root, in order. Each step prints what comes next. Nothing opens a browser.

1. `bash scripts/live-test/grafana-sandbox/steps/1_publish_to_grafana.sh`   puts 6 community dashboards in your Grafana
2. `bash .../2_show_in_grafana.sh`   lists them with links (read-only)
3. `bash .../3_extract_and_convert.sh`   reads them back and converts (offline after the read)
4. `bash .../4_preview_new_relic.sh`   shows what would be created (sends nothing)
5. `bash .../5_import_to_new_relic.sh`   creates private ckg- dashboards (asks for "yes")
6. `bash .../6_undo.sh`   previews, then removes what was created (asks for "yes")

Needs `.env` with GRAFANA_URL, GRAFANA_SA_TOKEN, NEW_RELIC_API_KEY, NEW_RELIC_ACCOUNT_ID.
Dashboard JSON is read from `out/live-grafana/src` (override with CKG_SRC).
