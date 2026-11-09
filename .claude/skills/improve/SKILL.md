---
name: improve
description: One cycle of the improvement loop. Measure conversion on the fixtures, pick the top blocker, fix it on a branch with a failing test first, check it with an independent reviewer, and stop for the owner's approval. Use when asked to improve conversion coverage or run the loop.
---

Read `docs/dev/improvement-loop.md` first. The loop proposes and tests; the owner approves.

1. Measure: `uv run importer improve-measure --record docs/improvement/history.jsonl`. Note the
   rate per source and the ranked blockers. A reason with "NO GUIDANCE" comes before everything
   else.
2. Pick one blocker: the most frequent one that code can fix. If the right answer is "stays
   manual" (no equivalent in New Relic), the fix is guidance, not conversion. If you are not
   sure the fix is possible, start the `researcher` subagent and cite its sources.
3. Never work on main. Start the `fixer` subagent with `isolation: worktree`, so it gets its own
   checkout on a new branch `improve/<short-slug>` and nothing else in progress can mix into it.
4. Give the `fixer` the single reason pattern, the branch name, and the measure.
5. Run `scripts/improve/gate.sh` yourself, inside the fixer's worktree. It must print GATE PASSED. Do not accept the
   subagent's word for it.
6. Start the `reviewer` subagent on `git diff main...HEAD`. Give it the diff, not your opinion.
   Fix serious findings (another `fixer` round) and re-run the gate. Stop after three rounds and
   report what is still open.
7. Measure again and write `docs/improvement/proposals/<date>-<slug>.md` with: the blocker, the
   change, before and after rates, the reviewer's findings and what was fixed, risks, and what
   you could not verify.
8. Stop. Show the owner the proposal and the diff summary. Merging, updating
   `docs/improvement/baseline.json`, regenerating golden files, pushing and any live-account
   test each need the owner's explicit yes. Never do them as part of the loop.

Another cycle starts only when the owner asks for it. Stop early and say why when the gate
cannot be made to pass, when a fix would lower another source's rate, or when the next blocker
needs real customer data.
