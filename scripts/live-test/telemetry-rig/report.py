"""Turn the comparison JSON into markdown tables (stdout). Usage: report.py COMPARE.json"""

import json
import sys
from pathlib import Path


def esc(text: str, n: int = 110) -> str:
    text = " ".join(str(text).split())
    return (text[: n - 1] + "…" if len(text) > n else text).replace("|", "\\|")


def main() -> None:
    data = json.loads(Path(sys.argv[1]).read_text())
    rows: dict[tuple[str, str], dict] = {}
    for r in data["results"]:
        rows.setdefault((r["variant"], r["id"]), {})[r["mode"]] = r
    for variant in ("plain", "hints"):
        print(f"\n### Variant: {variant}\n")
        print(
            "| case | PromQL | NRQL (chart) | chart vs dashboard range | chart vs 1m range | stat |"
        )
        print("|---|---|---|---|---|---|")
        for (v, cid), m in rows.items():
            if v != variant:
                continue
            c, s = m.get("chart", {}), m.get("stat", {})
            nrql = c.get("nrql") or c.get("detail", "")
            print(
                f"| {cid} | `{esc(c.get('promql', ''), 70)}` | `{esc(nrql, 120)}` | {c.get('status')} "
                f"| {c.get('status_1m', '')} | {s.get('status')} |"
            )


if __name__ == "__main__":
    main()
