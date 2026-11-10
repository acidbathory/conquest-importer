# Brief templates

Copy one, fill every `<...>`, delete nothing in the sections marked MANDATORY, and send it as the
whole prompt of the subagent. A subagent knows only what the brief says.

| Template | Use for | Writes code | Touches a live system |
|---|---|---|---|
| [fixer.md](fixer.md) | One conversion blocker, test first | yes | no |
| [reviewer.md](reviewer.md) | Independent review of a diff | no | no |
| [researcher.md](researcher.md) | A sourced answer from vendor docs | notes only | no |
| [live-test.md](live-test.md) | A sandbox check of something already converted | scripts only | yes, owner approves each run |
| [docs.md](docs.md) | Documentation that must stay true to the code | docs and doc tests | no |

Lessons that shaped them (from `docs/improvement/lessons.md`, the proposals and the day's logs):

- A brief that does not name the worktree command gets work done on main or in someone else's
  checkout. Name the command, the branch and the rule "work only there".
- A brief that does not list forbidden commands gets `terraform init`, `apply`, or a hand-written
  `curl` against New Relic. List them.
- A brief that says "make the tests pass" gets weakened tests. Say "never edit an expectation to
  fit new output; change the code or ask".
- A brief without a final message format gets a story. Ask for the facts the overseer needs to
  verify: the gate output, files changed, numbers before and after, open doubts.
- A hypothesis in the brief comes back confirmed. Give a reviewer the diff, not the conclusion.
- `/tmp` is shared between agents. Name a private folder.
