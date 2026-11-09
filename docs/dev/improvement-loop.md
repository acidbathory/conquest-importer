# The improvement loop

A cycle that finds what blocks conversion, fixes one thing, proves it did not make anything
worse, and then stops for the owner. It runs offline on the fixtures, so it costs nothing and
gives the same answer twice.

## One cycle

| Step | Who | Output |
|---|---|---|
| 1 Measure | `importer improve-measure --record docs/improvement/history.jsonl` | Rate per source, ranked blockers, reasons with no guidance |
| 2 Pick | The `improve` skill | One blocker: no guidance first, then the most frequent one code can fix |
| 3 Branch | The skill | `improve/<slug>` from main |
| 4 Fix | `fixer` subagent | A failing test, then the smallest change, then the gate |
| 5 Gate | `scripts/improve/gate.sh` run by the skill, not trusted from the subagent | GATE PASSED or not |
| 6 Review | `reviewer` subagent, given the diff only | Findings with inputs that break the change |
| 7 Propose | The skill | `docs/improvement/proposals/<date>-<slug>.md` with before and after rates |
| 8 Stop | | The owner decides |

## The gate

`scripts/improve/gate.sh` fails if any of these fails: ruff check, ruff format check,
`mypy --strict`, vulture, pytest (with resource warnings as errors), the measure compared with
`docs/improvement/baseline.json`, and a diff of `tests/golden/`.

A change counts as a regression when, against the baseline, a source converts fewer assets of the
same estate, has a lower rate on a changed estate, disappears, or gains a reason with no manual
steps. Passing the gate is necessary, not sufficient: the reviewer and the owner still decide.

## What the loop may not do

- Change `tests/golden/*` or `docs/improvement/baseline.json` (a person reviews and approves both).
- Push, merge, or work on main.
- Run `apply*`, `migrate`, `rollback`, Terraform or anything against a live cloud or New Relic
  account. Live tests are a separate step the owner approves, with the cost named first.
- Weaken a check, skip or delete a test, or raise a threshold to make a number pass.
- Use customer data. Fixtures are scrubbed and synthetic.

## Getting better at getting better

The loop also improves itself, with the same approval rule.

- Every cycle ends with lessons in `docs/improvement/lessons.md`: what went wrong and the
  check that would have caught it. If a class of defect appeared (rule shadowing, an unclosed
  client, a missing-metric condition that looks healthy), the next cycle's first job is an
  automated check for that class, not another instance fix.
- Checks only ratchet up. The gate may gain checks; removing or loosening one is a change to
  the project rules and needs the owner.
- Changes to the loop itself (measure, gate, skills, agents, these docs) are proposals like any
  other and are reviewed by the `reviewer` subagent first.
- The baseline moves forward only when the owner approves a proposal. It never moves backward.

## Evidence

Fixtures show whether conversion logic is right on known shapes. They do not show whether a
converted query returns data or fires. `check-gcp-data` and the live-test suite do that, against
a sandbox, when the owner approves. The loop reports that gap in every proposal.
