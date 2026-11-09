"""Small helpers for the Markdown reports of the dashboard and inventory plans."""


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    """A Markdown table. Every cell must already have gone through `md_cell`."""
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return lines + ["| " + " | ".join(row) + " |" for row in rows]
