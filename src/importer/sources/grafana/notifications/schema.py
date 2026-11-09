"""The contact point schema table: which settings of each integration type are secret.

The data file `contact_point_schema.json` follows the Grafana source (see the research note).
It is a table, not code: a new field is a new row and a test row. The running Grafana's
`integrationtypeschemas` endpoint is authoritative; this table is the offline copy.
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

SCHEMA_FILE = Path(__file__).with_name("contact_point_schema.json")


class FieldSpec(BaseModel):
    name: str
    secure: bool
    protected: bool
    required: bool
    kind: str
    values: list[str] = []  # option: the allowed values
    type: str = ""  # option: bool or int
    min: int = 0
    max: int = 0
    pattern: str = ""  # option: a fixed shape


class TypeSpec(BaseModel):
    v1: bool
    fields: list[FieldSpec]


@lru_cache(maxsize=1)
def load_schema() -> dict[str, TypeSpec]:
    raw = json.loads(SCHEMA_FILE.read_text())
    return {name: TypeSpec.model_validate(spec) for name, spec in raw["types"].items()}


def type_names() -> tuple[str, ...]:
    return tuple(load_schema())


def field_spec(type_name: str, path: str) -> FieldSpec | None:
    spec = load_schema().get(type_name)
    if spec is None:
        return None
    return next((f for f in spec.fields if f.name == path), None)


def secure_fields(type_name: str) -> frozenset[str]:
    spec = load_schema().get(type_name)
    return frozenset(f.name for f in spec.fields if f.secure) if spec else frozenset()
