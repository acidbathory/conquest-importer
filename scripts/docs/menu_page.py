"""Turn a menu transcript (from make_menu_run.py) into a styled HTML page for a screenshot.

Usage: python scripts/docs/menu_page.py <transcript.txt> <out.html> <menu number>
Paths and the sample account name are replaced so no local path or name appears.
"""

import html
import re
import sys
from pathlib import Path

PAGE = """<!doctype html><meta charset="utf-8"><style>
body{{margin:0;background:#1c1b17;padding:28px}}
.t{{width:1040px;background:#11110f;border:1px solid #3a3933;border-radius:8px;overflow:hidden}}
.b{{background:#2a2924;color:#a8a396;font:13px -apple-system,sans-serif;padding:8px 14px}}
pre{{margin:0;padding:16px 20px;color:#e8e4d8;font:14px/1.45 Menlo,monospace;white-space:pre-wrap}}
</style><div class="t">
<div class="b">Terminal output: rendered from a scripted run on sample data</div>
<pre>{body}</pre></div>"""


def scrub(text: str) -> str:
    text = re.sub(
        r"(file://)?/(private/)?tmp/[^\s]*?/(?=(grafana|dynatrace|gcp)-\d)", r"\1out/guided/", text
    )
    text = re.sub(r"\b(grafana|dynatrace|gcp)-\d{8}-\d{6}", r"\1-<timestamp>", text)
    return text.replace("account 7 (Sandbox Team)", "account <account-id> (<account name>)")


def main(src: Path, out: Path, choice: str) -> None:
    text = scrub(src.read_text(encoding="utf-8"))
    text = text[text.index("Conquest Importer") :]
    text = text.replace("Your choice:", f"Your choice: {choice}", 1)
    text = text.split("Nothing was created.")[0] + "Nothing was created.\n"
    text = re.sub(
        r"Type 'yes' to continue, anything else cancels:\n",
        "Type 'yes' to continue, anything else cancels: no\n",
        text,
    )
    out.write_text(PAGE.format(body=html.escape(text.strip("\n"))), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
