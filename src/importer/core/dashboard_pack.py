"""Move boxes that overlap on the dashboard grid down until none does (shared by the converters)."""

from importer.core.dashboard_ir import Layout


def overlaps(a: Layout, b: Layout) -> bool:
    return (
        a.column < b.column + b.width
        and b.column < a.column + a.width
        and a.row < b.row + b.height
        and b.row < a.row + a.height
    )


def pack[T](placed: list[tuple[T, Layout]]) -> tuple[list[tuple[T, Layout]], int]:
    """Reading order, with any widget that overlaps an earlier one moved down below it.

    Returns the widgets and how many were moved. Each pass moves a widget past at least one more
    box, so the loop ends after at most one pass per box already placed.
    """
    taken: list[Layout] = []
    out: list[tuple[T, Layout]] = []
    moved = 0
    for widget, layout in sorted(placed, key=lambda p: (p[1].row, p[1].column)):
        row = layout.row
        for _ in range(len(taken) + 1):
            hit = [t for t in taken if overlaps(t, layout.model_copy(update={"row": row}))]
            if not hit:
                break
            row = max(t.row + t.height for t in hit)
        if row != layout.row:
            moved += 1
            layout = layout.model_copy(update={"row": row})
        taken.append(layout)
        out.append((widget, layout))
    return out, moved
