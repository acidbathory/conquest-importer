"""Ask New Relic whether the NRQL of every converted rule case is valid (reads only).

Usage:
    SSL_CERT_FILE=/tmp/q/ca.pem uv run python scripts/analysis/promql_rule_validate.py

Each converted case of `promql_rule_cases.json` is run through NerdGraph `nrql` with
`SINCE 1 hour ago` added when the query has no time range. Nothing is created or changed; the
query is a GraphQL variable. The result goes to `docs/research/promql-engine-validity.json`
(case id, a hash of the query, accepted or the first error), which
`tests/test_promql_engine_rules.py` compares with the current output so that a status of
VERIFIED-VALID in the rules registry cannot outlive a change of the query. Values are not compared
here: that is the differential test the registry marks as pending.

Keys come from the environment or the repository .env (parsed, never printed).
"""

import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts" / "improve"))

import verify_cookbook  # noqa: E402
from promql_rule_runner import load_cases, run_case  # noqa: E402

OUT = ROOT / "docs" / "research" / "promql-engine-validity.json"
VARIABLE = re.compile(r"\{\{\s*\w+\s*\}\}")


def digest(nrql: str) -> str:
    return hashlib.sha256(nrql.encode()).hexdigest()[:16]


def checkable(nrql: str) -> str:
    """The query as a widget would run it: a dashboard variable becomes a literal."""
    query = VARIABLE.sub("'x'", nrql)
    return query if re.search(r"\bSINCE\b", query, re.IGNORECASE) else query + " SINCE 1 hour ago"


def main() -> int:
    verify_cookbook.load_env()
    record: dict[str, dict[str, object]] = {}
    for case in load_cases():
        got = run_case(case)
        if not got["converted"]:
            continue
        entry: dict[str, object] = {
            "sha": digest(got["nrql"]),
            "date": datetime.now(tz=UTC).date().isoformat(),
        }
        try:
            verify_cookbook.nrql(checkable(got["nrql"]))
            entry["accepted"] = True
        except RuntimeError as error:
            entry.update(accepted=False, error=str(error)[:200])
        record[case["id"]] = entry
        print(("accepted " if entry["accepted"] else "REJECTED ") + case["id"])
    OUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return 0 if all(e["accepted"] for e in record.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
