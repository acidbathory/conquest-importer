---
name: oversee
description: Run one cycle of the overseer: scoreboard, diagnosis, the next three dispatches with briefs, then wait for the owner's go. Use when asked to oversee the project, check whether progress is real, find what to do next, or run the self-improvement cycle. Add "autonomous" to dispatch without waiting.
---

Read `docs/dev/overseer.md`. This skill runs one cycle and stops; the owner starts the next.

1. Measure. Save the old file: `mkdir -p /tmp/overseer && cp docs/improvement/scoreboard.json /tmp/overseer/previous.json`
   (if it exists). Then
   `uv run importer scoreboard --compare /tmp/overseer/previous.json --ci` with `--in <folder>` for
   each converter output folder under `out/` that is current (ask the owner if unsure which).
   Show the verdict line and the stall line exactly as printed.
2. Diagnose. Read the remediation top 5, `parity.json` verdicts, the `not_proven` list in
   `docs/improvement/live-evidence.json`, the CI line, open proposals, `git worktree list` and
   unmerged branches. Run the census when dashboards are available:
   `uv run python scripts/analysis/promql_census.py <dashboards>`.
3. Prioritise. Update `docs/improvement/next-actions.md` (value, effort, risk, dependency). Pick
   the next three dispatches. Say what was left out and why.
4. Brief. For each dispatch, fill the matching template in `docs/dev/brief-templates/` completely
   and show it.
5. Wait. Show the owner: the verdict, the three dispatches, what needs their approval (live runs,
   baselines, golden files, pushes). Stop. Do not start subagents until the owner says go, unless
   the owner said "autonomous".
6. After the jobs return (owner go or autonomous): run the gate in each worktree yourself, start
   the `reviewer`, re-measure, write lessons and proposals, and report in at most eight lines with
   bad news first. If there is a stall, the first line says so and proposes a different lever.

Never push or merge, never change golden, hash or baseline files, never touch a live system
without the owner's yes in this session, never add AI attribution anywhere.
