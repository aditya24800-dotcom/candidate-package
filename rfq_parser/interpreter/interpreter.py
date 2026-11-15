"""Turn the model's answer for one email into the record data/README.md defines.

Two jobs:
  1. shape: the fields the contract asks for, and its rules about which classes carry lines
     and review flags;
  2. catalog: for each line, the one stocked part it names (matched), the finding that we do
     not stock it (not_in_catalog), or the finding that several stocked parts fit what the
     customer wrote (ambiguous).

The catalog decision is made by code alone, from attributes the customer stated. An
attribute the customer did not state never rules a part out, and never picks one: if the
parts that fit differ in it, the answer is ambiguous. No line is ever removed here.
"""

from __future__ import annotations

from pathlib import Path

from .attributes import Attributes, parse, spec_kind
from .catalog import Part, load_catalog

REQUEST_CLASSES = ("rfq", "rfq_revision", "purchase_order")
MAX_CANDIDATES = 12


class Interpreter:
    def __init__(self, catalog_path: Path | None = None):
        self.parts = load_catalog(catalog_path)
        self.by_product: dict[str, list[Part]] = {}
        for part in self.parts:
            self.by_product.setdefault(part.product, []).append(part)

    # --- the whole answer -----------------------------------------------------------------

    def interpret(self, answer: dict) -> dict:
        """`answer` is what llm.read_email returns. The result is one entry of parsed.json."""
        classification = answer["classification"]
        is_request = classification in REQUEST_CLASSES
        lines = [self.interpret_line(line) for line in answer["line_items"]] if is_request else []
        needs_review = bool(answer["needs_review"]) and is_request
        reason = answer["review_reason"] if needs_review else None
        if is_request and not lines and not needs_review:
            # A request with nothing to quote must reach a person, never pass as "done".
            needs_review, reason = True, "The email reads as a request but no line items were found."
        if needs_review and not (reason or "").strip():
            reason = "The reader flagged this email for review without giving a reason."
        return {
            "classification": classification,
            "revises": None,
            "duplicate_of": None,
            "needs_review": needs_review,
            "review_reason": reason,
            "line_items": lines,
        }

    @staticmethod
    def duplicate_record(original: dict, original_id: str) -> dict:
        """The record for an email that repeats `original`: same class, the link, and no lines."""
        return {
            "classification": original["classification"],
            "revises": None,
            "duplicate_of": original_id,
            "needs_review": False,
            "review_reason": None,
            "line_items": [],
        }

    def interpret_line(self, line: dict) -> dict:
        qty = line["qty"]
        if isinstance(qty, float) and qty.is_integer():
            qty = int(qty)
        return {
            "raw": line["raw"],
            "description": line["description"],
            "qty": qty,
            "uom": line["uom"],
            "source": line["source"],
            **self.match(line["description"]),
        }

    # --- one line against the catalog -----------------------------------------------------

    def match(self, description: str) -> dict:
        wanted = parse(description)
        if not wanted.products:
            return _not_in_catalog("no stocked product type is named in the description")
        parts = [p for product in wanted.products for p in self.by_product.get(product, [])]
        name = " or ".join(wanted.products)

        sized = self._same_size(parts, wanted)
        if not sized:
            return _not_in_catalog(f"{name} is stocked, but not in size {_show_size(wanted.size)}")
        fits = sized
        for label, test, stated in (
            ("spec", self._spec_fits, _show(wanted.specs)),
            ("material", self._material_fits, _show(wanted.material)),
            ("ends", self._ends_fit, " and ".join(_show_ends(e) for e in wanted.ends)),
            ("feature", self._extras_fit, _show(wanted.extras)),
        ):
            narrowed = [p for p in fits if test(p, wanted, parts)]
            if not narrowed:
                stocked = _show({getattr(p, "extra" if label == "feature" else label) or "none" for p in fits})
                size = _show_size(wanted.size)
                return _not_in_catalog(f"{name} {size} is stocked, but not with {label} {stated} (stocked: {stocked})")
            fits = narrowed

        if len(fits) == 1:
            return {"sku": fits[0].sku, "match": "matched", "match_reason": f"one part fits: {fits[0].description}"}
        differing = [
            f"{label} ({_show({getattr(p, attr) or 'none' for p in fits})})"
            for label, attr in (("product", "product"), ("spec", "spec"), ("material", "material"), ("ends", "ends"), ("feature", "extra"))
            if len({getattr(p, attr) for p in fits}) > 1
        ]
        if len({p.size for p in fits}) > 1:
            differing.insert(0, f"size ({_show({_show_size(p.size) for p in fits})})")
        return {
            "sku": None,
            "match": "ambiguous",
            "match_reason": f"{len(fits)} stocked parts fit; the customer must choose {' and '.join(differing)}",
            "candidates": sorted(p.sku for p in fits)[:MAX_CANDIDATES],
        }

    @staticmethod
    def _same_size(parts: list[Part], wanted: Attributes) -> list[Part]:
        if not wanted.size:
            # No size stated: a part sold without a size is the exact one; otherwise any size fits.
            return [p for p in parts if not p.size] or parts
        sizes = {wanted.size, wanted.size[::-1]}  # "3 x 1" and "1 x 3" name the same nipple
        return [p for p in parts if p.size in sizes]

    @staticmethod
    def _spec_fits(part: Part, wanted: Attributes, parts: list[Part]) -> bool:
        kinds = {spec_kind(p.spec) for p in parts} - {""}  # the kinds of spec this product has
        stated = {s for s in wanted.specs if spec_kind(s) in kinds}
        return not stated or part.spec in stated

    @staticmethod
    def _material_fits(part: Part, wanted: Attributes, parts: list[Part]) -> bool:
        if not any(p.material for p in parts):
            return True  # this product is not sold by material
        return wanted.material <= part.material_facts

    @staticmethod
    def _ends_fit(part: Part, wanted: Attributes, parts: list[Part]) -> bool:
        if not any(p.ends for p in parts):
            return True
        return all(part.ends in acceptable for acceptable in wanted.ends)

    @staticmethod
    def _extras_fit(part: Part, wanted: Attributes, parts: list[Part]) -> bool:
        return wanted.extras <= part.extra_facts


def _not_in_catalog(reason: str) -> dict:
    return {"sku": None, "match": "not_in_catalog", "match_reason": reason}


def _show(values) -> str:
    return ", ".join(sorted(str(v) for v in values))


def _show_ends(acceptable: frozenset) -> str:
    if acceptable == {"THD", "TBE", "T&C", "TOE"}:
        return "threaded"
    return "flanged" if acceptable == {"RF", "FF", "RTJ"} else _show(acceptable)


def _show_size(size: tuple) -> str:
    def one(value):
        if isinstance(value, str):
            return value
        whole, fraction = int(value), value - int(value)
        for denominator in (2, 4, 8, 16):
            if abs(fraction * denominator - round(fraction * denominator)) < 1e-9:
                numerator = round(fraction * denominator)
                text = f"{numerator}/{denominator}" if numerator else ""
                return f"{whole}-{text}" if whole and text else text or str(whole)
        return str(value)

    return " x ".join(one(v) for v in size) or "(none stated)"
