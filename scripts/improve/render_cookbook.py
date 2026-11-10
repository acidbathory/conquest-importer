"""Write docs/user/remediation-cookbook.md from the cookbook data. Run after editing the data."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from importer.core.cookbook.render import GUIDE_PATH, render_markdown  # noqa: E402

(ROOT / GUIDE_PATH).write_text(render_markdown(), encoding="utf-8")
print(f"wrote {GUIDE_PATH}")
