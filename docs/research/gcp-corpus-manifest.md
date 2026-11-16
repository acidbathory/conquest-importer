# GCP corpus manifest (2026-11-16)

| Repo | URL | Licence (LICENSE file) | Commit |
|---|---|---|---|
| monitoring-dashboard-samples | https://github.com/GoogleCloudPlatform/monitoring-dashboard-samples | Apache-2.0 | 2ef05daf347a39a9c9afe9accba464799a06b637 |
| googleapis (sparse: google/monitoring, google/api) | https://github.com/googleapis/googleapis | Apache-2.0 | 62746790cb5c0fe1956e7f2df8422d3a7f00ba15 |
| terraform-google-cloud-operations (read, no policies used) | https://github.com/terraform-google-modules/terraform-google-cloud-operations | Apache-2.0 | 8c3e37951bb4c8aad973fbbe7afa3e3238ef052b |
| professional-services (blobless, grep found no policy or dashboard JSON; not used) | https://github.com/GoogleCloudPlatform/professional-services | not read | 587120aa12c7017e1bf481f97e356b3628747691 |

Clones are under /tmp/community/gcp-src/. Helper scripts are in /tmp/community-tools/.

Corpus built:
- Dashboards: 142 files of monitoring-dashboard-samples dashboards/*/*.json not in tests/fixtures/gcp-dash or the gcp-estate manifest (fresh). Input: /tmp/community/gcp-corpus/in-dash. Output: /tmp/community/gcp-out/dash.
- Alerts: all 276 alerts/*/*.json of the same commit. All 276 are already in gcp-estate (manifest, same tree SHA), so they are NOT fresh. Input: /tmp/community/gcp-corpus/in-alerts-all. Output: /tmp/community/gcp-out/alerts-all.
- Experiment inputs (rewrites of in-dash, throwaway): in-dash-novars, in-dash-ot, in-dash-rate.
Converter: worktree integrate-1111 at 269ac33 (it may lag main's PromQL engine merge; not checked).
