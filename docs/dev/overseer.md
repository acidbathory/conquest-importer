# The overseer: how the project improves itself and says so when it does not

The overseer is a role, not a process. It is the `overseer` subagent (`.claude/agents/overseer.md`)
started by the `/oversee` skill, and everything it uses is in this repository: the scoreboard, the
gate, the brief templates, the lessons. Nothing runs in the background and nothing is hidden. The
owner can read every input, repeat every step by hand, and stop it at any point.

It extends the single-fix loop in [improvement-loop.md](improvement-loop.md) from "fix one
blocker" to "decide which blocker, who works on it, how it is checked, and whether the whole effort
is still moving".

## The loop

| Step | What the overseer does | Tool or file | Output |
|---|---|---|---|
| 1 MEASURE | Run the scoreboard | `uv run importer scoreboard --compare <last scoreboard.json>` | `docs/improvement/scoreboard.md`, `.json`, one line in `scoreboard-history.csv`, MOVED / NOT MOVED per metric |
| 2 DIAGNOSE | Find what blocks the metrics | census (`scripts/analysis/promql_census.py`), remediation top 5 (`importer plan-remediation`), `parity.json` verdicts, `docs/improvement/live-evidence.json` (what is not proven), the last CI run (`--ci`), open proposals and `HANDOFF.md` | A short list of blockers, each with a number |
| 3 PRIORITISE | Rank by value per effort, risk and dependency | `docs/improvement/next-actions.md` | The next three dispatches, in order, with the reason for each and what was left out |
| 4 DISPATCH | Start isolated subagents | briefs from `docs/dev/brief-templates/` (fixer, reviewer, researcher, live-test, docs), each in its own worktree | One branch per job, `improve/<slug>` or `research/<slug>` |
| 5 VERIFY | Check the result without trusting the report | `scripts/improve/gate.sh` run by the overseer, the parity verifier when it exists, the live harness (owner approves each run), an independent `reviewer` given the diff only | GATE PASSED or not; findings with the input that breaks the change |
| 6 LEARN | Record what went wrong and the check that would have caught it | `docs/improvement/lessons.d/<date>-<slug>.md`, a proposal in `docs/improvement/proposals/`, and a baseline proposal when a rate moved | Files for the owner to approve |
| 7 REPORT | Tell the owner in a few lines | the chat | What moved, what did not, what is blocked on the owner, and any stall |

The owner approves: baselines, golden files, hash files, pushes and merges, every live run, and
every deployment. The overseer proposes all of them and does none of them.

## What "improving" means

A change is progress only if an outcome metric on the scoreboard moved in the good direction and
nothing else got worse:

- more alerts converted, fewer manual or unsupported;
- more dashboard widgets converted, fewer placeholders ("Not migrated" notes);
- more parity verdicts `match`, fewer `mismatch`;
- more items in `live-evidence.json` with a date, a method and a path to the record.

Not progress: more tests, more documentation, more code, a higher "dashboards created" count, or a
placeholder counted as a converted widget. The scoreboard reports tests as information only.
Converted means a real chart or condition; a placeholder is its own line. Items that convert only once the customer sends the metric (GCP: Ops Agent, Prometheus, custom) carry a `data_path`; the scoreboard counts them in `*_datapath_<class>` and `*_datapath_known`, which are information, never part of the converted figure. The headline percentage in
the report counts a dashboard as migrated when it can be created even if most widgets are
placeholders; the overseer quotes widgets, never that percentage, until the owner decides.

## Stall detector

A stall is: no outcome metric moved over K cycles. K is 3 unless the owner says otherwise.

- Outcome metrics are the ones with a direction: `alerts_converted`, `alerts_manual`,
  `alerts_unsupported`, `widgets_converted`, `widgets_placeholders`, `parity_match`,
  `parity_mismatch`, `verified` (live). Test counts and anything else are information.
- It is computed from `scoreboard-history.csv`: the trailing run of identical outcome values is
  at least K runs long. `importer scoreboard` prints the line itself:
  `STALLED: no outcome metric has moved over the last 3 cycles (unchanged since <date>)`.
- With `--compare`, the plain verdict is `No outcome metric moved since <date>.`
- When stalled the overseer says so as the first line of its report, names the lever that was
  tried, and proposes a different one. It does not dispatch a fourth job on the same lever.
  Levers, in the order to try them: fix the largest blocker by count; fix the blocker that gates
  the most panels (a gate such as manual variables can hide hundreds of items); get live
  evidence for something already converted; add the missing measurement (a parity verifier, a
  census); ask the owner for data or a decision; stop.

## Stop conditions

Stop the cycle and report when any of these holds:

1. The gate cannot be made to pass without loosening a check.
2. A fix would lower another source's rate.
3. The next item needs real customer data, a credential the repo does not have, or a decision
   only the owner can make.
4. Three review rounds on one branch left serious findings open.
5. A stall was reported and the owner has not chosen a new lever.
6. Anything unexpected happened in a live system (an object not created by us changed, a secret
   appeared in output). Stop, say exactly what was seen, touch nothing more.

## Safety rules the repo has proven

- Create-only in the New Relic sandbox and the test clouds: only new objects named `ckg-`
  (`ckg_test=true` on test metrics), only removal of what a manifest says this run created, and a
  re-read after every delete. Never modify or delete a pre-existing entity.
- Preview first: dry run is the default; a write needs `--apply` plus `--confirm-account`.
- Owner approval, per session and for the exact diff, before changing `tests/golden/*`,
  `tests/fixtures/*-hashes.json` or `docs/improvement/baseline.json`; before any push or merge;
  before any live run (state the cost first).
- No secrets: keys live in `.env`; never print, log, commit or paste them. Scan the diff before any
  push.
- No AI attribution: no `Co-Authored-By` trailer, no "Generated with" line, in commits, pull
  requests, files or docs. Check `git log --format=%B | grep -i co-authored` prints nothing.
- No bypass of network controls or of the permission system.
- Work only in your own worktree; never on main; never touch another worktree.

## Anti-patterns met so far

| Anti-pattern | What happened | What the overseer does |
|---|---|---|
| An agent loosens a test to pass | A fixer changed an expectation instead of the code | The reviewer is told to look for weakened assertions; the diff of `tests/` is read before the diff of `src/`; the briefs forbid it |
| Flaky verification | A live check passed once and failed on the next run (scrape timing, window rounding) | A claim needs the method and the numbers in `live-evidence.json`; a check is repeated once before it counts; an unexplained difference is recorded as such |
| Progress by counting placeholders | Pages created looked like pages converted (56 percent against about 43 percent by widget) | The scoreboard counts placeholders on their own line and never as converted |
| CI stricter than the local gate | Lint on `scripts/` passed locally and failed on GitHub | The gate runs the same commands as `.github/workflows/ci.yml`; `--ci` shows the real result; a CI difference is fixed in the gate first |
| Hand-sent API writes blocked by the permission classifier | A direct `curl` or ad hoc script that wrote to New Relic was denied three times | Use the project commands (`apply`, `apply-dashboards`, scripts in `scripts/live-test/`) that carry the guards; never work around a denial; report it and ask the owner |
| A research note read as verified fact | A function was assumed to exist on `Metric` data | Evidence tags (MEASURED, DOCS, UNVERIFIED) and a live check before conversion code relies on it |
| Reviewers or builders running things they should not | One reviewer ran `terraform init` | Review briefs list the forbidden commands; reviewers are read-only |
| Shared `/tmp` between agents | Two agents wrote to the same temporary file | Briefs name a private folder, `/tmp/<slug>/` |
| Stale branches | A branch finished against an old base | Rebase and re-run the gate before merging; `overlap.sh` before any merge |

## Before proposing anything

1. Check that it is not already done: `docs/improvement/next-actions.md` ("Done"),
   `docs/improvement/live-evidence.json`, `docs/improvement/scoreboard-history.csv`, and
   `git log --oneline origin/main` (grep for the topic). Also `git branch --no-merged origin/main`
   for work in progress elsewhere.
2. Never copy a number from a research note or an older backlog. Measure it again with the commands
   in `next-actions.md` ("How the numbers were taken") and write the date next to it. A census from
   before a fix describes a project that no longer exists (the "8 widgets end to end" figure was
   removed by the chained-variables work). `tests/test_next_actions.py` fails if a backlog item has
   no measurement date or the Done section is missing.

## The parity.json contract

The parity verifier writes `parity.json` into the converter output folder it checked (next to
`ir/` or `dashboard-ir/`). The scoreboard reads exactly one of these three forms and ignores
anything else with a warning:

```json
{"source": "grafana", "verdicts": {"match": 12, "mismatch": 2, "not_comparable": 5}}
```
```json
{"source": "grafana", "items": [{"name": "cpu", "verdict": "match", "checked_on": "2026-11-10"}]}
```
```json
[{"verdict": "match"}, {"verdict": "mismatch"}]
```

Rules: `verdict` is a string; counts are integers; `source` (optional) is the source system name,
and when absent the folder's only source is used. Verdicts are lower-cased by the reader.
`match` / `matched` / `verified` / `pass` count as progress; `mismatch` / `diverge` / `fail` count
as regressions; any other word (for example `not_comparable`, `no_data`) is reported but is
information only. Each item should carry `checked_on` and the method in the verifier's own report.
The scoreboard writes `out/<source>/parity_<verdict>` metrics.

## Dispatching

Every dispatch is a brief copied from `docs/dev/brief-templates/` with the blanks filled in. The
mandatory sections are: worktree creation command, scope, authorisations, forbidden actions, gate,
final message format. A brief without them is not sent. Rules for parallel work are in
[parallel-work.md](parallel-work.md); at most three jobs run at once, and jobs that own the same
files run one after the other.

The overseer never accepts a subagent's report as the result. It runs the gate itself in the
worktree, reads the diff summary, and starts a `reviewer` that is given the diff and not the
conclusion.

## Reporting

The report is at most eight lines: what moved (numbers), what did not, what is blocked on the
owner (approvals, data, decisions), the stall line if any, and the next three dispatches. Bad news
comes first. "Not proven" is a valid and expected status.

## Running one cycle

```
uv run importer scoreboard --in out/grafana-alerts --in out/grafana-dash \
    --compare docs/improvement/scoreboard.json --ci
```

Then `/oversee` (the skill) does steps 2 to 7 and stops for the owner's go, or runs unattended
only when the owner says "autonomous", in which case it still stops at every approval in the
list above.
