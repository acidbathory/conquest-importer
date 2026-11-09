"""Reasons that only the conversion step produces. The read side's reasons are in
`importer.sources.grafana.notifications.reasons`; these follow the same rules: fixed sentences,
no text from the export (names, addresses and URLs never appear in a reason), and none starts
with a phrase that a read-side taxonomy pattern already claims.
"""

R_SECRET = "grafana converted destination needs a secret supplied at apply time"  # noqa: S105 - a sentence
R_NO_ADDRESS = "grafana converted email destination needs an address"
R_UNKNOWN_RECEIVER = "grafana notification policy receiver is not in the export"
R_UNKNOWN_TIMING = "grafana notification policy names a mute timing that is not in the export"
R_NO_DESTINATION = "grafana notification route has no destination that can be created"
R_MAP_MISMATCH = "grafana destinations map entry does not fit the contact point"
R_VENDOR_UNVERIFIED = "grafana vendor webhook payload was not tested against the service"
R_MUTE_SCOPE = "grafana muting rule from a mute timing covers the whole condition"
R_ORPHAN_MUTE = "grafana unused mute timing was not converted"
