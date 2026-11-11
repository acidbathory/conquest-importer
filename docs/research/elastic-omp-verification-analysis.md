# Verification and test strategy of Elastic's observability migration platform, and what we can learn

Date: 2026-11-11. Status: research note, nothing in `src/` changed.

## Clean-room statement

The public repository `elastic/observability-migration-platform` is under the Elastic License 2.0. It is not open source and the licence cannot be sublicensed, while this project is Apache-2.0. This note was written after reading the repository as untrusted data (downloaded as a tarball into a scratch directory outside our tree, nothing executed, nothing installed, scratch directory deleted afterwards). Everything below is a description in our own words, plus short identifiers (file names, verdict names, command names). No code, comment, doc text, fixture, dataset, configuration or compose file was copied. Every design in section 3 is expressed in our terms and must be implemented from this note and from our own code, not from their files. A person who has read their source in detail should not write the implementation of a proposal that mirrors one of their modules too closely; the owner can decide who implements.

## 1. How their parity rig works

Their target is Elastic (Elasticsearch plus Kibana), their sources are Grafana and Datadog dashboards. The rig lives in `parity-rig/` and has four layers.

1. A data plane. A small deterministic Prometheus-format producer (counters advance at fixed rates, so reruns give the same numbers; a second endpoint fakes a Kubernetes slice), a real node exporter, and Prometheus scraping them. Prometheus remote-writes the same samples into Elasticsearch. A reference Prometheus and the target therefore hold the same numbers. Everything runs from a compose file; a "curated" subfolder builds a heavier per-dashboard environment (Redis, MySQL, Postgres exporters) for one dashboard at a time.
2. A query harness (`harness/parity.py`). For each migrated panel it takes the original PromQL, fills Grafana template variables from a table of defaults, and runs the query on Prometheus (`query_range`) and on Elasticsearch. Two modes: an identity mode, where the exact same PromQL string runs through Elasticsearch's native PromQL command (so the translator is not under test, the platform is), and a fallback mode, where the translated ES|QL runs against the PromQL answer from Prometheus. Series are keyed by label set after dropping labels that only one side adds (job, instance, external labels), time buckets are aligned, boundary buckets are trimmed, and a per-bucket relative error is computed.
3. Verdicts and tolerances. Strict pass at 1 percent, fuzzy pass at 5 percent, a "shape" pass when labels overlap and error is up to 25 percent (they note that this name is not numeric proof), no-overlap failure, error, skip. Each result is tagged with the mode. The README lists known external limitations (unsupported operators in the preview PromQL command, calendar-aligned buckets, `le` label canonicalisation) so that a failure can be explained rather than argued about. A separate script runs the translator against the native PromQL command on one index with no Prometheus at all, using the native command as an independent oracle.
4. A static verifier package (`verifier/`) with a tiered model. For every panel it records the query text at six stages (source, emitted, intermediate representation, stored by the target's dashboard API, stored saved object, live execution) and flags drift between adjacent stages only. It states explicitly which tiers were not checked ("reported, not inferred"), carries a per-tier population block so "agreed" is never confused with "never looked", has an offline linter for chart wiring (does every dimension refer to a column the query returns), a mutation module that corrupts good reports to prove the verifier fails on each known bug class, a benchmark-history gate (success percentages must not drop against the last comparable run), and a fidelity ratchet (error counts per category must not rise against a committed baseline; improvements are always allowed).

Around that: a pinned third-party corpus (manifest with repo and commit SHA, plus 69 grafana.com dashboards pinned by id, revision and sha256, fetched on demand, never committed), visual/interaction audits in a browser, and nightly live gates in CI.

### Test suite

- About 1,000 tracked files; tests are flat plus `core/`, `e2e/`, `targets/`, `curated/`, `fixtures/`, `snapshots/`. A shared conftest migrates the committed corpora once per session and the e2e tests score them against the baselines.
- Snapshots: translated-query text files named by case and feasibility (`..._feasible.txt`, `..._not_feasible.txt`), regenerated only with an environment switch (`UPDATE_SNAPSHOTS=1`); a missing snapshot is written on first run.
- Property tests (Hypothesis): no crash, label conservation (grouping labels survive or the loss is disclosed), determinism. Metamorphic tests: whitespace, label order and matcher order must not change the translation. A fuzz test for the Datadog parser. A structural oracle that parses emitted ES|QL.
- No coverage gate that we could find beyond having the plugin installed; no mutmut-style mutation testing of the code itself (their "mutations" test the verifier, not the source). Matrix across three Python versions.
- Unsupported handling: unsupported is a first-class snapshot class (case files marked not-feasible), a skip verdict in the rig, and an explicit reason on panels degraded to a placeholder (checked by the linter).
- Idempotency and upload safety: a few dedicated tests (composite breakdown idempotent, empty-dashboard upload report, smoke test of uploaded dashboards, alert upload verification script). It is a thinner area than ours (we have rollback, run store, create-only rules).

### CI

Separate workflows for quality (ruff, mypy on a targeted set, CLI smoke, pytest on 3.11 to 3.13), licence and SBOM, secret scanning, nightly live gates, render and interaction audits, release. Licence workflow: an allowlist/denylist check over the locked environment, a committed human-readable inventory and a committed CycloneDX SBOM, both regenerated in CI and failing on drift; per-package overrides must cite the upstream licence URL that was inspected. Source files carry licence headers and a script checks them. Secret scan: gitleaks with the default rules plus a tiny allowlist, run over full history on every push and pull request, binary downloaded at a pinned version.

## 2. Comparison with our verification

| Area | Theirs | Ours | Gap |
|---|---|---|---|
| Reference system | Prometheus fed by a deterministic producer, same samples remote-written to the target | Real Prometheus 3 + node_exporter + an 11-port synthetic exporter (kube-state-metrics, JVM, cAdvisor, Postgres, NGINX, flaky and missing targets), remote write to New Relic, counter resets, late series, `up` | Ours is richer in exporter families and realistic dynamics; theirs is reproducible (fixed rates). Ours depends on wall clock and a laptop that sleeps |
| Platform vs translator | Identity mode isolates the platform from the translator, plus an independent oracle (native PromQL command) | No independent PromQL oracle on the target side (NRQL has no PromQL mode); Prometheus is the oracle | Not copyable as is; the equivalent is differential testing against a second PromQL implementation offline |
| Comparison semantics | Label-set key, drop one-sided labels, trim boundary buckets, relative error | Explicit bucket-end to bucket-begin shift, first and last bucket dropped, subset label match for facets, best shift search up to 2 buckets, absolute tolerance, ratio cause analysis, verified/close fractions (95 and 75 percent) with a minimum point count | Ours is more careful. Theirs has a reusable "known limitation" list |
| Verdict vocabulary | strict, fuzzy, shape, no-overlap, error, skip, plus mode | verified, close, mismatch, no data, not comparable, with causes and per-series results | Comparable. Their "shape" bucket is deliberately weak; ours does not have one, which is better |
| Static drift check | Six-tier same-text check across stages, with explicit unchecked-tier reporting | Offline byte-identity and hash tests of emitted dashboards, golden CloudWatch file, IR tests; no read-back-from-the-target tier in the parity verifier | We have live round-trip notes but no automated "what the target stored equals what we sent" check |
| Chart wiring linter | Offline invariants (columns referenced exist, no silent label merges) | Partly in dashboard tests | Possible small win (see item 9) |
| Verifier self-test | Mutations that corrupt a good report and demand a finding | None | Gap |
| Regression ratchet | Committed per-category error baselines, improvements allowed; history gate | `baseline.json`, scoreboard with MOVED / NOT MOVED, history csv, gate.sh refuses golden changes | Equivalent; ours is stronger on "progress must be real", theirs on per-category ratchets |
| Corpus | 69 pinned community dashboards plus manifest SHAs and hash verification | Six community dashboards from the owner's estate, a few repos listed in research notes | Gap: a pinned, hash-verified public corpus |
| Property and metamorphic tests | Hypothesis in three files | Hypothesis is not a dependency of ours | Gap |
| Live evidence | Nightly live gates in CI | `live-evidence.json` with dates, method and evidence path; every live run owner-approved; measured quirks (metric type inference, `metricName` collision, counters without `_total`) | Ours has real-world findings that theirs does not and a stricter honesty rule. Ours is manual |
| Replay | Deterministic producer is the replay | None; live runs cannot be reproduced offline | Gap |
| Secret scan | gitleaks in CI over history | Rules in CLAUDE.md, `.env` ignored, `pip-audit` | Gap in automation |
| Licence / SBOM | Allowlist/denylist, inventory, SBOM, header check | `license = Apache-2.0` in pyproject; nothing automated | Gap |
| Type/lint gate | ruff, targeted mypy | ruff, ruff format, `mypy --strict src tests`, vulture, yamllint, actionlint, terraform fmt, tflint, pip-audit | Ours is stricter |

What we have that they do not: the explicit alignment rules written down with their reasons; a verdict that carries a cause; the type-inference experiment (same PromQL, typed versus untyped remote write) that found the largest class of wrong answers; the "sum() of a gauge" finding turned into a refusal; the owner-approval discipline and dated evidence file; golden-file protection in the gate.

## 3. Twelve improvements to our verification, ranked

Ranking is value divided by effort, then by risk. "Clean-room design" is written in our terms; none depends on their code.

1. **Offline differential harness for the PromQL rewrites (rules2 and the converter).** Design: a small reference evaluator (instant and range) for the subset of PromQL we convert, written from the PromQL specification, plus an evaluator for the NRQL shapes the converter emits, both running over the same in-memory sample set in a test. Compare per bucket using the existing alignment code. The sample set comes from item 2. Every rewrite rule gets a case where the rewritten and the original forms must agree, and cases where they must not agree are recorded as known divergences with a reason (window smoothing, `topk` ranking, `irate`). Effort: large (about 2 weeks); start with `rate`, `increase`, ratios, `histogram_quantile`. Risk: medium, the evaluator can share a misreading with the converter, so write it from the spec and check it against recorded Prometheus answers from the rig (item 3). Offline, no credentials, runs in the gate.
2. **Deterministic data generators.** Design: a seeded generator module (counters with resets, gauges with waves, histograms with cumulative buckets, info metrics, late and absent series, gaps) that produces samples as a pure function of (seed, time). The rig exporter and the offline harness both use it, so a live run can be reproduced exactly offline. Effort: medium (3 days; the exporter already has the shapes). Risk: low.
3. **Record and replay.** Design: when the rig compares, write the Prometheus answer, the NRQL text, the NR answer and the window into a small JSON fixture (no keys, no account id). A replay test re-judges stored fixtures with the current `judge` code, so tolerance or alignment changes are tested against real answers. Fixtures are reviewed by a person like golden files. Effort: small (1 day). Risk: low; fixtures must be scrubbed of account identifiers.
4. **Tolerance rules as data, with named reasons.** Design: one table (in code, tested) mapping cause names to the tolerance and the expected verdict, for example window-smoothing against a 5 minute range gets a mean-ratio rule, a counter without `_total` is a known mismatch class, `topk` is series-diff. Today these live in prose in the rig results. Add a rule that an expectation with no cause fails the test. Effort: small. Risk: low.
5. **"Verified on date" labels everywhere a claim is made.** Design: reports, the cookbook and `what-migrates.md` get their "verified" wording only from `live-evidence.json`; each item has id, date, method, evidence path, and an expiry (for example 90 days) after which the scoreboard shows it as stale. A test fails if a doc says verified for an id not in the file or past expiry. Effort: small. Risk: low. We already have most of the file format.
6. **Pinned public corpus with hash verification.** Design: a manifest listing upstream URL, commit or revision, sha256 and licence for each public dashboard (see section 5), a fetch script that downloads and verifies, never committed, and a nightly or manual job that runs convert on all of them and reports counts per reason. Effort: medium. Risk: low; licence column must be filled before anything is vendored.
7. **Ratchet per category on the corpus.** Design: a committed JSON of counts per reason (converted, placeholder by reason, error) for the corpus; the gate fails if an error category rises, allows improvement, and the baseline is updated only by a human. This extends the existing `baseline.json`. Effort: small once item 6 exists. Risk: low.
8. **Property and metamorphic tests with Hypothesis.** Design: add `hypothesis` as a dev dependency; generate PromQL from a small grammar (aggregation, by-labels, matchers, range functions); assert no crash, determinism, label conservation (grouping labels reach the FACET or a warning is emitted), whitespace, label-order and matcher-order insensitivity, and that every refusal carries a reason. Effort: medium. Risk: low; keep the example count small so the gate stays fast, and pin a seed in the gate.
9. **Mutation tests for the verifier itself.** Design: a test module that takes a good parity result and a good dashboard conversion, damages them in targeted ways (drop a label, shift a bucket, scale by 1.1, swap two series, empty one side) and asserts the verifier reports the matching verdict and cause. Also run a mutation tool over `parity/align.py` occasionally (manual, not in the gate). Effort: small to medium. Risk: low.
10. **Read-back tier for dashboards and alerts.** Design: after `apply-dashboards` or alert apply in the sandbox, read the object back through NerdGraph and compare the query text and thresholds with what was sent; report tiers not checked explicitly with counts, so "agreed" and "not looked" can be told apart. Effort: medium. Risk: medium (live, owner-approved runs only, create-only rules already in force).
11. **Reproducible rig.** Design: replace laptop sleep sensitivity with a clock the exporter owns (counters advance on a virtual clock tied to scrape number), keep the watchdog, pin tool versions and checksums (already done) and print them into the results note automatically. Effort: small. Risk: low.
12. **Chart and query wiring linter plus idempotency/upload-safety tests.** Design: offline checks that each converted dashboard widget's facet, alias and variable names exist in its query, that a placeholder widget always has a reason, and that applying the same plan twice creates nothing the second time (fake transport counting create calls). Effort: medium. Risk: low.

## 4. Test-suite practices worth adopting

- Fixture provenance: every third-party fixture needs a manifest entry (upstream URL, revision, sha256, licence, date fetched, who reviewed). A test asserts that every file under `tests/fixtures/` that is not ours is in the manifest. Their approach of committing only a pin for large third-party sets, and fetching on demand, avoids both bulk and licence questions; we should do the same for public dashboards.
- Snapshot approval flow: one environment switch regenerates, a missing snapshot is an error in CI (not silently written), and snapshot files are plain text named for the outcome class so a review diff shows feasibility changes. We already protect golden files in gate.sh; add the same refusal for any `snapshots/` directory we create.
- Ratchets with human-only baseline updates (item 7), and per-category rather than total counts so a fix in one place cannot hide a regression in another.
- Self-tests of the checker (item 9) and explicit "not checked" reporting (item 10).
- Secret scanning: add a gitleaks job (pinned version, full history, default rules, tiny allowlist for documented placeholders) and a pre-commit hook. It belongs next to the existing rule that keys live only in `.env`. Also scan docs under `docs/research`, which contain live-run output.
- Licence and SBOM in CI, details below.
- Version matrix: they test three Python versions; we test 3.12 and 3.14 already.
- Targeted mypy is weaker than our strict setting; do not copy that.

### Proposed licence check for our repository

Goal: catch (a) a dependency whose licence is incompatible with Apache-2.0 distribution, (b) third-party text or data copied into the tree with an incompatible or unknown licence, (c) source files that carry another project's licence marker. Their gate covers only (a) and header presence; (b) and (c) are what would have caught a conflict like mixing Elastic-licensed material into an Apache-2.0 repo.

New script `scripts/check_licences.py`, run in the `check` job and in the gate:

1. Dependencies: read the licence of every package in the locked environment (`pip-licenses` JSON or `importlib.metadata`); allow Apache-2.0, MIT, BSD, ISC, PSF, MPL-2.0, Unlicense, CC0; deny AGPL, GPL, SSPL, BUSL, Elastic-2.0, Commons Clause, Confluent Community, any "non-commercial" or "source-available" label; unknown fails unless listed in an override table whose entries must carry the upstream URL that was inspected. Treat LGPL as a reviewed exception, not a default allow.
2. Tracked-file marker scan: fail if any tracked file (excluding this script and its test, and `docs/research` text that names licences) contains markers such as `SPDX-License-Identifier:` with a value other than Apache-2.0 or an allowed third-party one, `Elastic License`, `Elastic-2.0`, `Server Side Public License`, `Business Source License`, `Commons Clause`, `All rights reserved` in a header. Files under `third_party/` or fixture directories may carry their own licence only if listed in the provenance manifest.
3. Provenance manifest `THIRD_PARTY.md` (or `tests/fixtures/PROVENANCE.json`): every vendored or fixture file group from outside names upstream URL, licence, copyright line. A test fails for a non-first-party fixture directory not in the manifest, and for a manifest licence outside the allow list.
4. Fingerprint guard: a short list of reference strings that must not appear (module names or distinctive identifiers from restricted-licence projects we studied, kept in a file that holds only identifiers, never code). This catches an accidental paste after a clean-room read.
5. SBOM: generate CycloneDX in CI, upload as an artifact, and fail on drift against a committed copy only if the owner wants that noise; the artifact alone is enough to start.
6. Inventory drift: the generated licence table is committed and CI fails when it differs, as they do.

Effort: medium (about 2 days). Risk: low; false positives in item 2 are handled by an explicit allow list with reasons.

## 5. Third-party fixtures and datasets they use (upstream URLs and licences only)

Fetch these from upstream ourselves, record revision and sha256, and check the licence at fetch time. Their stated licence is given where they state one; verify upstream.

| Material | Upstream | Licence as stated by them |
|---|---|---|
| Node Exporter Full dashboard and companion service dashboards | https://github.com/rfmoz/grafana-dashboards (also https://grafana.com/grafana/dashboards/1860) | Unlicense in one of their files, Apache-2.0 in another: they disagree, so check the upstream LICENSE file ourselves |
| Kubernetes views dashboards | https://github.com/dotdc/grafana-dashboards-kubernetes (https://grafana.com/grafana/dashboards/15757) | Apache-2.0 |
| Prometheus 2.0 overview dashboard | https://github.com/FUSAKLA/Prometheus2-grafana-dashboard (https://grafana.com/grafana/dashboards/3681) | MIT |
| kube-prometheus mixin dashboards | https://github.com/prometheus-operator/kube-prometheus | Apache-2.0 |
| Datadog integration dashboards (docker, kubernetes, redis, nginx, postgres) | https://github.com/DataDog/integrations-core | BSD-3-Clause |
| 69 community Grafana dashboards (Kubernetes cluster monitoring, deployment and pod metrics, Redis exporter 763, Docker, Node Exporter Full and others), chosen by download count plus regression seeds | https://grafana.com/grafana/dashboards/ (ids and revisions in their pin file) | Per dashboard; grafana.com does not state one uniformly. Treat as unknown until the dashboard page or repository says otherwise, do not redistribute |
| Dashboard schema generator and YAML-to-Lens tooling | https://github.com/strawgate/kb-yaml-to-lens | MIT |
| Kibana Dashboards API OpenAPI description | Elastic's hosted spec for the Kibana Dashboards API | Not stated by them; irrelevant to us (New Relic target) |
| Container images used by their rigs | https://hub.docker.com/r/prom/prometheus, https://hub.docker.com/r/prom/node-exporter, https://hub.docker.com/r/grafana/grafana-oss, https://github.com/oliver006/redis_exporter, https://github.com/prometheus-community/postgres_exporter, https://github.com/prometheus/mysqld_exporter | Apache-2.0 for Prometheus projects and exporters; Grafana OSS is AGPL-3.0 (run it, do not vendor or link it) |

Their producer, curated environments, scenario files, snapshots and baselines are their own work under the Elastic License 2.0 and are not to be used.

## 6. Licence verdict

Reading and describing the repository is fine; copying anything from it into our Apache-2.0 tree is not. Nothing in this note includes their text or code. The third-party sets they use are mostly permissive (Apache-2.0, MIT, BSD-3-Clause, Unlicense) and can be fetched from upstream directly; the one to treat carefully is the grafana.com community set, whose per-dashboard licence is not uniform.
