"""Small shared constants and helpers used by every source."""

import hashlib

MAX_INPUT_BYTES = 50 * 1024 * 1024  # largest export file any loader reads


def source_id(key: str) -> int:
    """A stable integer id derived from a source key (used for file names and resource labels)."""
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)
