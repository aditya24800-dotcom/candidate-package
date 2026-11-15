"""JSON schemas for the model's answers (OpenAI strict structured outputs).

Strict mode needs every property listed in `required` and `additionalProperties: false`.
The class and unit lists come from the contract's own checker, so they cannot drift.
"""

from __future__ import annotations

from data.check_format import CLASSES, UOMS

LINE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "raw": {"type": "string"},
        "description": {"type": "string"},
        "qty": {"type": ["number", "null"]},
        # anyOf, not a nullable enum: Anthropic rejects an enum whose type is ["string", "null"].
        "uom": {"anyOf": [{"type": "string", "enum": list(UOMS)}, {"type": "null"}]},
        "source": {"type": "string"},
    },
    "required": ["raw", "description", "qty", "uom", "source"],
    "additionalProperties": False,
}

# `summary` comes first so the model states what the message does before it picks a class.
EMAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "classification": {"type": "string", "enum": list(CLASSES)},
        "needs_review": {"type": "boolean"},
        "review_reason": {"type": ["string", "null"]},
        "line_items": {"type": "array", "items": LINE_ITEM_SCHEMA},
    },
    "required": ["summary", "classification", "needs_review", "review_reason", "line_items"],
    "additionalProperties": False,
}

TRANSCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["document", "logo_or_signature", "other"]},
        "legible": {"type": "boolean"},
        "note": {"type": ["string", "null"]},
        "text": {"type": "string"},
    },
    "required": ["kind", "legible", "note", "text"],
    "additionalProperties": False,
}
