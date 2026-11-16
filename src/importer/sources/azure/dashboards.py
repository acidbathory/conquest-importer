"""Azure dashboards (workbooks and portal dashboards) as one dashboard source."""

from importer.core.dashboard_ir import IRDashboard
from importer.core.text import printable
from importer.sources.azure.dashboard_models import AzureDashboard
from importer.sources.azure.portal_dashboard import translate_portal
from importer.sources.azure.workbook import translate_workbook


def _translate(dash: AzureDashboard) -> IRDashboard:
    """One dashboard; one that cannot be translated becomes one unsupported entry, not a crash."""
    try:
        return translate_portal(dash) if dash.kind == "portal" else translate_workbook(dash)
    except ValueError as exc:  # a pydantic validation error is a ValueError
        failed = dash.model_copy(update={"content": None, "error": printable(str(exc), 160)})
        return translate_portal(failed) if dash.kind == "portal" else translate_workbook(failed)


def translate_dashboards(dashboards: list[AzureDashboard]) -> list[IRDashboard]:
    """Translate all; a repeated source key gets `#2`, `#3` so each dashboard has its own file."""
    out: list[IRDashboard] = []
    used: set[str] = set()
    for dash in dashboards:
        translated = _translate(dash)
        key, n = translated.source_key, 1
        while translated.source_key in used:
            n += 1
            translated = translated.model_copy(update={"source_key": f"{key}#{n}"})
        used.add(translated.source_key)
        out.append(translated)
    return out
