# Azure workbook KQL gaps (2026-11-11)

- The five gaps named in the previous lesson turned out to be a small share of the real queries:
  the `let` cases are mostly `join`/`union`/`toscalar`, which have no NRQL form. One public
  workbook widget converted (273 to 274 of 458).
- Inlining a table `let` is exact only when it is used once; count uses, do not guess.
- KQL integer division differs from NRQL: accept `/` only where an operand is a decimal literal
  and the divisor is a literal. Check the type, not just the syntax.
- `=~` stays manual (no verified case-insensitive equality); it now has its own steps.
