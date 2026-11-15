"""The catalog, loaded once, with each part's attributes in the same form a line is read into."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .attributes import CATALOG_MATERIALS, parse_catalog_extra, parse_catalog_size

DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "data" / "catalog.csv"


@dataclass(frozen=True)
class Part:
    sku: str
    description: str
    product: str
    size: tuple
    spec: str
    material: str
    ends: str
    extra: str
    material_facts: frozenset
    extra_facts: frozenset


def load_catalog(path: Path | None = None) -> list[Part]:
    parts = []
    with open(path or DEFAULT_CATALOG, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            material = row["material"].strip()
            # A material the table does not know is compared word for word.
            facts = CATALOG_MATERIALS.get(material, set(material.lower().split()))
            parts.append(
                Part(
                    sku=row["sku"].strip(),
                    description=row["description"].strip(),
                    product=row["product"].strip(),
                    size=parse_catalog_size(row["size"]),
                    spec=row["spec"].strip(),
                    material=material,
                    ends=row["ends"].strip(),
                    extra=row["extra"].strip(),
                    material_facts=frozenset(facts),
                    extra_facts=parse_catalog_extra(row["extra"]),
                )
            )
    return parts
