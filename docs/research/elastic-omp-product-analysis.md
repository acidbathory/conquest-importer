# Elastic's Observability Migration Platform: product and workflow analysis

Written 2026-11-11. Clean-room note: the upstream repository is under the Elastic License 2.0 (source
available, not open source, not sublicensable). Our project is Apache-2.0. This file was written from
reading the public repository (README, docs, workflow files, Makefile, pre-commit and agent files)
and describes what was seen in our own words. Nothing was copied, no upstream code was run or
installed, and the scratch download was deleted afterwards. The ideas below are product and process
ideas, which are not copyrightable; any implementation must be written from scratch from our own
sketches. Do not reopen the upstream files while implementing.

Scope: the product and workflow side only. The translation engine internals (PromQL to ES|QL) are
out of scope here.

## 1. What the platform is and does

A Python package (`elastic-observability-migration` on PyPI, console command `obs-migrate`) that moves
Grafana and Datadog dashboards, alerts and monitors into Kibana (Elastic Serverless or Stack 9.5+).
It reads from exported JSON files or from the source's API, translates queries and panels, and
writes reviewable artifacts: the exact payloads the Kibana Dashboards API takes, an intermediate
representation of each dashboard, and a set of reports. It can then upload them. Alerts become
Kibana alerting rules that are created disabled and tagged, with empty actions, so nothing notifies
anyone until a person turns them on.

The headline promise: what cannot be translated is marked for manual review instead of being dropped
silently. It is the same promise our tool makes.

Distribution is `uvx --from '<package>[all]' obs-migrate ...` with no clone needed. The first-run path
is `doctor`, then `list-samples` (sample dashboards bundled in the wheel), then an offline `migrate`
on one sample, then review, then `upload`.

## 2. Architecture overview (our words)

- **Source adapters in, shared target runtime out.** Each source (Grafana, Datadog) has an adapter
  package that does extraction, normalisation and query/panel translation. Raw vendor JSON never
  leaves the adapter.
- **Typed intermediate representation.** Adapters build a dashboard IR made of panel, query, visual
  and operational parts, plus controls, alerts, annotations and links. The Kibana payload is derived
  from the IR, not the other way round. The IR is also written to disk next to the payload.
- **A single status vocabulary** across sources (translated, translated with warnings, manual
  required, not feasible, blocked, skipped), with per-source names mapped onto it.
- **An orthogonal second axis, "target binding".** Status says whether the query was translated
  faithfully. Binding says whether the label fields the query now uses were verified against the
  target's real field capabilities, asserted by the operator, guessed from a default, or found
  missing. It is shown in the summary and per panel and control in the JSON report.
- **Kibana target module** emits panels, layout and display, performs the typed upload, and runs
  smoke checks and a browser render audit after upload.
- **Extension points**: rule packs (YAML), Python plugins, a metric-map file, field profiles, and
  bundled "curated packs" for well-known community dashboards that override query semantics or layout.
  A command prints the extension schema and a starter template.
- **Data flow**: export or API -> extract and normalise -> translate -> IR -> native payload ->
  optional live validation against Elasticsearch -> artifacts on disk -> optional upload -> optional
  smoke and render audit -> reports.
- **Output layout** under the output directory: `dashboards/native/*.native.json` and an index,
  `dashboards/ir/*.ir.json`, `migration_summary.md` (verdict, scorecard, per-dashboard table, must-fix
  list, grouped warnings), `migration_report.json`, `migration_manifest.json`,
  `verification_packets.json`, `rollout_plan.json`; `alerts/` with rule artifacts and upload results;
  a root `run_summary.json` recording which asset families ran and why anything was skipped.
- **Command surface**: `doctor`, `list-samples`, `migrate` (with `--assets dashboards|alerts|all`,
  `--input-mode files|api`, `--validate`, `--preflight`, `--upload`, `--create-alert-rules`,
  metadata selection flags), `upload`, `cluster` (list, ensure data views, delete), `verify`,
  `compare` (side-by-side numeric parity), `schema-report`, `audit-rules`, `verify-alert-rules`
  (self-cleaning round trip), `seed-sample-data`, `remove-sample-data`, `metric-map`, `extensions`.
- **Verification as layers** rather than one percentage: unit and snapshot tests, a supported-type
  registry with a matrix test per type, a fidelity "ratchet" against a committed baseline, a schema
  gate against the pinned Kibana OpenAPI document, and nightly live gates (runtime query validation,
  typed API contract, render audit, control-interaction audit, corpus and benchmark gates).
- **Repo hygiene tooling**: Makefile with a self-documenting help target, locked `uv` environment,
  pre-commit (secret scan, header check, ruff, quick test, no hard-coded local paths), license
  allowlist gate plus CycloneDX SBOM with drift checks in CI, trusted-publishing release workflow
  (tag must match the package version, SBOM attached to the release).

## 3. What they do better than us

| Area (where seen) | What they do |
|---|---|
| Packaging and first run (README, command contract, release workflow) | Published on PyPI, run with `uvx` and no clone; wheel bundles sample dashboards; `list-samples` prints a ready-to-run command for each. Release uses OIDC trusted publishing and a tag/version consistency check. |
| Doctor and exit-code contract (command contract) | Exit codes are written down per situation in a table (for example "rule creation requested but impossible" is `2` up front, `1` afterwards, never a quiet `0`). We document "exit 2 if not" per check command, but not as one contract. |
| Fail-loud on requested-but-not-done (alert rule creation section) | A requested action that did not happen fails the run, and the reason is stored in `run_summary.json` so CI keeps the evidence. |
| Second axis of trust: target binding (asset model doc) | Separates "translated" from "fields proven to exist in the target". Our statuses mix translation fidelity with whether the data arrives; the latter lives in separate `check-*-data` commands. |
| Run summary for humans (`migration_summary.md`) | One generated Markdown page with verdict, scorecard, worklist and grouped warnings; best-effort so a render failure never fails the run. |
| Layered tests with a registry (testing doc) | A supported-type registry whose coverage is enforced by a test: adding a panel type without a matrix cell fails CI. Fidelity ratchet against a committed baseline. |
| Supply chain (license-check workflow, `THIRD_PARTY_NOTICES`) | Dependency license allowlist and denylist gate, generated dependency inventory and CycloneDX SBOM that fail CI on drift, SBOM attached to releases, third-party notices file with upstream, licence and copyright per bundled file. Our CI runs `pip-audit` only. |
| Secret scanning (pre-commit, gitleaks config, SECURITY.md) | Secret scanner in pre-commit and a dedicated workflow, with a documented placeholder allowlist. SECURITY.md has a clear "report privately vs. open an issue" split. |
| Skills checked as code (`check_skill_*` scripts) | Agent skills are validated: front matter parses, name equals folder, description length capped, cross-references resolve, referenced files exist, and every quoted CLI command in a skill uses a real subcommand and real flags. |
| Credentials ergonomics (`*_creds.env.example`) | One example file per system (serverless, Grafana, Datadog), with canonical variable names plus documented aliases. We have a single `.env.example`. |
| TLS flags (command contract) | `--ca-cert` (verification stays on) and a loudly-warned `--insecure`, applied to every outbound connection, with environment fallbacks. We use `SSL_CERT_FILE` plus truststore. |
| Docs governance (AGENTS.md) | An explicit "which doc must change for which kind of change" checklist, and an operator-first rule: a feature is not done if the happy path needs repo-only helper scripts. |
| Open problems doc (`docs/design/open-problems.md`) | Known-unsolved issues kept with evidence and how they were found, with FIXED entries kept for history. |

## 4. What we do better

- **Safety posture is stricter.** Everything we create is disabled or private, the source side is
  read-only by construction (GET only, host allow-list), account ids are allow-listed, and
  `importer rollback` removes exactly what a run created, tracked in a run record. Their revert is
  by hand: dashboard deletion by id with no dry run and rule deletion by tag.
- **More sources and the notification path.** We convert notifications and workflows (SNS, channels,
  Terraform ingestion), and cover CloudWatch, GCP, Dynatrace, Azure and Grafana. Theirs is two
  sources, and alert actions are left empty.
- **Evidence discipline.** `docs/improvement/live-evidence.json`, the verification log and the
  UNVERIFIED label stop us claiming what was never seen live. They have more automation but less
  explicit "not proven" bookkeeping in what we saw.
- **A self-improvement loop.** Scoreboard with history, baseline file, gate script that refuses
  golden changes, overseer role with briefs and lessons. Their loop is CI ratchets; ours also steers
  which job comes next.
- **Real-data checks per source** (`check-*-data`, `verify-parity` against Prometheus) and a guided
  menu with HTML report and sample-data mode for non-programmers.
- **Licence**: Apache-2.0 with no source-available restriction.
- **Smaller attack surface**: a handful of runtime dependencies and `mypy --strict` over src and tests.

## 5. Ranked ideas to adopt (15)

Effort S/M/L, risk is the risk of the change itself. "Sketch" is our own design.

1. **Written exit-code contract and fail-loud rule.** Why: green-looking runs that did nothing are
   the worst failure. Now: exit codes appear per command in `docs/user/script-reference.md` and in
   `src/importer/cli.py` (`typer.Exit(code=1)` sites) with no single table. Sketch: define a small
   enum in `src/importer/core` (OK, FAILED, BLOCKED_BY_INPUT, NOTHING_DONE_THAT_WAS_ASKED), use it
   in every command, add a test that walks the Typer app and asserts each command only returns
   enum values, and publish one table in the docs. S, low.
2. **`run-summary.json` that records every requested-but-skipped action.** Why: CI and the menu can
   show why something did not happen. Now: run records exist for rollback (`docs/user/menu-and-rollback.md`)
   but not a skipped-with-reason ledger. Sketch: each stage appends `{stage, requested, done, reason}`;
   `--apply` with zero creations and a non-empty plan exits non-zero unless every item was
   deliberately manual. S, low.
3. **Second trust axis: "data binding" per converted item.** Why: translated is not the same as
   arrives. Now: `check-*-data` commands and `docs/improvement/live-evidence.json` hold this apart
   from the converter output. Sketch: add an optional `binding` field on each IR item with values
   verified (seen in NRQL `keyset()` or a data check), asserted (operator metric map), guessed
   (default naming), missing; fill it from the data checks when they have run; show counts in the
   report and HTML. M, low (additive field, golden files need human review).
4. **Dependency and licence gate plus CycloneDX SBOM in CI.** Why: customers ask; we redistribute
   nothing yet but ship via pip. Now: `.github/workflows/ci.yml` runs `pip-audit` only. Sketch: a
   script that reads installed metadata, checks licences against an allowlist and a denylist, writes
   a Markdown inventory; generate an SBOM with the CycloneDX tool; fail CI on drift. S to M, low.
5. **Secret scanning in pre-commit and CI.** Why: `.env` holds live keys and Grafana tokens now. Now:
   `.pre-commit-config.yaml` has ruff, mypy, vulture, terraform fmt, yamllint, actionlint but no
   secret scanner; `.gitignore` only excludes the files. Sketch: add gitleaks (Apache-2.0 upstream)
   as a hook and a CI job with a project config allow-listing test placeholders such as fake
   `NRAK-` strings; add a SECURITY.md with private-reporting instructions. S, low.
6. **Publish to PyPI via trusted publishing, run with `uvx`.** Why: zero-clone first run is the
   single biggest adoption lever for a field tool. Now: `pyproject.toml` has the entry point
   `importer`, `scripts/migrate.sh` needs a checkout. Sketch: rename check (the name `importer` is
   too generic for PyPI; use `conquest-importer`), tag-triggered workflow building sdist and wheel,
   test that the tag equals the version, publish with OIDC, attach SBOM. Needs the owner's decision
   on public release. M, medium (name, support burden).
7. **Bundle sample inputs in the wheel plus `list-samples`.** Why: lets anyone try the tool offline in
   a minute. Now: samples sit in `tests/fixtures` and the menu's item 7 (not in the wheel). Sketch:
   copy a small sanitised set into `src/importer/samples/`, add package data, a `samples` command
   that prints paths and the exact command to run, and a test that every listed sample converts.
   S, low.
8. **Skill linter: structure and quoted-command validity.** Why: our skills (`.claude/skills/*`) quote
   `importer` commands that will rot as flags change. Now: no check. Sketch: a script that parses
   front matter, checks name equals folder and a description length cap, checks referenced `docs/`
   and `scripts/` paths exist, and runs every `importer ...` line from fenced blocks through Typer's
   help (`--help` parse only, never execute). Add to `scripts/improve/gate.sh`. S, low.
9. **Supported-type registry with an enforced matrix.** Why: it makes coverage claims in
   `docs/what-migrates.md` testable. Now: that doc is hand-written, with the golden file for
   CloudWatch only. Sketch: one registry per source listing each alarm type or widget type and its
   expected status; a test that fails when a handler exists without an entry or an entry without a
   fixture; generate the support tables in the docs from the registry. M, low.
10. **Fidelity ratchet beyond goldens.** Why: it catches slow decay without freezing exact output.
    Now: `tests/golden/cloudwatch_expected.json` and `docs/improvement/baseline.json` with
    `improve-measure`. Sketch: extend the baseline to count per-source statuses and fail on any
    drop in converted share or any rise in "silently dropped" fields, with a documented, human-only
    refresh step. Already half there; this is mostly extending to Grafana and GCP. S, low.
11. **Human summary page generated for every run.** Why: the first thing a customer reads. Now: HTML
    report exists via the menu (`docs/user/menu-and-rollback.md`), Markdown from several commands.
    Sketch: make `convert` always write `summary.md` with verdict, counts by status, a must-fix list
    ordered by effort, and grouped warnings, rendered through `md_cell`, best-effort so a failure
    never fails the run. S, low.
12. **Per-system credential example files and a doctor that checks them.** Why: fewer first-run
    failures. Now: one `.env.example`; `importer doctor --source` exists (`src/importer/core/doctor.py`).
    Sketch: split into `aws.env.example`, `gcp.env.example`, `grafana.env.example`,
    `newrelic.env.example`, keep `.env` loading unchanged, make doctor print which file and variable
    is missing, never a value. S, low.
13. **`--ca-cert` flag and `--insecure` with a loud warning.** Why: corporate TLS inspection is the
    common blocker; `SSL_CERT_FILE` is invisible to users. Now: `truststore` plus `SSL_CERT_FILE`
    in docs. Sketch: a global option on the Typer app that builds one shared `httpx` context, with
    an environment fallback; `--insecure` prints a one-time warning and is refused with `--apply`.
    S, medium (touches every HTTP client; our rule is never to weaken verification, so consider
    leaving `--insecure` out entirely).
14. **Docs-change checklist and an "open problems" file.** Why: keeps docs in step and records dead
    ends. Now: `CONTRIBUTING.md`, `docs/dev/*`, `docs/improvement/lessons.md`. Sketch: add a short
    table to CONTRIBUTING mapping change type to doc file; a CI check that a diff touching
    `src/importer/cli.py` also touches `docs/user/script-reference.md`; move unsolved issues
    into `docs/dev/open-problems.md` with the evidence. S, low.
15. **Curated, pinned sample corpus and community-dashboard packs.** Why: the best test input is real
    public dashboards; per-dashboard fix packs lift the share that converts cleanly. Now: Grafana
    fixtures and `docs/research/grafana-complex-dashboards-repos.md`. Sketch: a fetch script that
    pins upstream commit hashes and checks licences before download (section 6), kept out of the
    repo and used only for measurement; later, small per-dashboard metric/label maps keyed by the
    grafana.com id. L, medium (licences, maintenance).

## 6. Licence note

Their `THIRD_PARTY_NOTICES.md` lists what they redistribute, each with its own licence text under
`licenses/`. Whether the upstream licences hold was not independently re-checked; confirm on the
upstream repository on the day of any fetch.

| Material | Their stated licence | Original upstream we could fetch from |
|---|---|---|
| Five Datadog integration dashboards (Docker, Kubernetes, Redis, NGINX, Postgres) | BSD-3-Clause | `DataDog/integrations-core`, dashboard assets of each integration |
| Node Exporter Full (grafana.com id 1860) | Apache-2.0 | `rfmoz/grafana-dashboards` or the grafana.com listing |
| Kubernetes Views Global (id 15757) | Apache-2.0 | `dotdc/grafana-dashboards-kubernetes` |
| Prometheus 2.0 Overview (id 3681) | MIT | `FUSAKLA/Prometheus2-grafana-dashboard` |
| Dashboards JSON schema for the Lens YAML tool | MIT | `strawgate/kb-yaml-to-lens` (PyPI `kb-dashboard-core`) |
| Kibana Dashboards API OpenAPI bundle | Elastic's hosted spec (pinned copy) | Elastic's published Kibana API documentation; check terms before copying |

All of the dashboards above are under permissive licences (BSD-3, Apache-2.0, MIT) and can be fetched
by us from those upstream sources, not from Elastic's repository. Obligations: keep the licence text
and copyright line, do not use the owners' names to imply endorsement, and record each file in a
notices file of our own. The first-party engine, docs, tests and everything else in their repository
are ELv2 and must not be copied or adapted. Their curated packs, rule-pack examples, skills and
design docs are ELv2 too. Our policy: fetch only from the original upstream, pin a commit, store
under `tests/fixtures/third_party/` or fetch at test time, and add a `THIRD_PARTY_NOTICES.md`.

## 7. The AI-agent workflow files

What exists: `AGENTS.md` (the main rules), `CLAUDE.md` (a thin pointer plus Claude-only notes),
mirrored skill trees for three tools (`.claude/skills`, `.cursor/skills`, `.agents/skills`), a Codex
config with one prompt-submit hook that runs the code-graph helper, a `.mcp.json` registering that
code-graph server over stdio, and a `.codegraph` folder with only an ignore file. There are 18 skills,
all for operators of the published CLI (install, connect, assess, migrate, explain gaps, validate,
revert, cut over), not for developing the repo.

Worth adopting, in our terms:

- **Mirror and structure checks for skills** (idea 8). Their lint scripts keep the copies identical
  and validate skills mechanically. We have one tree, so only the structure and command checks apply.
- **Operator-first rule.** A feature is not done if its happy path needs repo-only scripts. Add one
  line to our `CLAUDE.md` and to the `fixer` and `reviewer` briefs under `docs/dev/brief-templates/`.
- **Commit workflow rules for agents.** Commit only when asked, never skip hooks, fix by new commit
  not amend, inspect divergence before pushing, never force-push main. We already forbid
  attribution; add these to `docs/dev/parallel-work.md`.
- **Read-only, artifact-driven skills.** Their explain-gaps and revert skills read files already on
  disk, state which fields they read, and spell out destructive-step confirmation. Our
  `migrate-aws`/`migrate-gcp` skills could cite the exact report fields in the same way, and a
  new `explain-gaps` skill could read our `ir/` and `report` output.
- **Hook idea, not their hook.** Their only hook injects code-graph context. A better one for us
  is a pre-tool-use guard in `.claude/settings.json` that blocks edits under `tests/golden/` and
  writes to `.env`, and a stop hook that runs `scripts/improve/gate.sh` before a session may report
  "done". This reflects rules already in `CLAUDE.md`, enforced by the harness instead of by memory.
- **Not worth adopting:** the code-graph MCP dependency (extra binary for a repo this size), and
  duplicate trees for tools we do not use.
