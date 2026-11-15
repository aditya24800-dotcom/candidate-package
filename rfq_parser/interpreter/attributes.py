"""Read a line's description into the attributes the catalog is keyed on.

    product   which kind of part (may be two when the wording leaves it open, e.g. "elbow")
    size      numbers, e.g. (2.0,) or (6.0, 4.0) or (0.75, "close")
    specs     schedules and pressure ratings the customer stated, in catalog spelling
    material  facts about the material the customer stated ("316", "galv", "a105", ...)
    ends      one set of acceptable catalog end codes per end condition the customer stated
    extras    features the customer stated ("lr", "13cr", "full port", ...)

Only what the customer wrote is recorded. An attribute they did not mention stays empty, and
the matcher treats it as "any".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SUBSTITUTIONS = (
    ("″", '"'), ("”", '"'), ("“", '"'), ("′", "'"), ("’", "'"),
    ("½", " 1/2"), ("¼", " 1/4"), ("¾", " 3/4"), ("°", " deg "),
    ("×", " x "), ("–", "-"), ("—", "-"),
)  # fmt: skip


def clean(description: str) -> str:
    """Lower-case text with typographic marks turned into the plain ones the patterns expect."""
    for old, new in _SUBSTITUTIONS:
        description = description.replace(old, new)
    return description.lower()


@dataclass
class Attributes:
    products: tuple = ()
    size: tuple = ()
    specs: frozenset = frozenset()
    material: frozenset = frozenset()
    ends: list = field(default_factory=list)
    extras: frozenset = frozenset()


# --- product ------------------------------------------------------------------------------

_FLANGE_WORD = re.compile(r"\b(?:flanges?|flg|flgs)\b")
# An elbow is often named by its angle alone: "90s", "45 deg", "90-degree fittings", "90 LR".
_ELBOW_WORD = re.compile(r"\b(?:elbows?|ells?|els?)\b|\b(?:90|45)[\s-]*(?:deg\w*|lr\b|sr\b)|\b(?:90|45)'?s\b")

# First match wins, so the specific names come before the words they contain.
_PRODUCTS = (
    ("stud bolt", r"\bstuds?\b"),
    ("hex cap screw", r"cap screws?|\bhcs\b|hex (?:head )?bolts?"),
    ("heavy hex nut", r"heavy hex nuts?|\bhh nuts?|\b2h nuts?"),
    ("channel spring nut", r"spring nuts?|channel nuts?|strut nuts?"),
    ("hex nut", r"hex nuts?"),
    ("threaded rod", r"threaded rod|all[\s-]*thread|\batr\b"),
    ("strut channel", r"\bstrut\b|unistrut"),
    ("pipe clamp", r"pipe clamps?|strut clamps?"),
    ("clevis hanger", r"clevis"),
    ("pipe thread dope", r"thread dope|pipe dope|thread sealant"),
    ("ptfe thread tape", r"thread tape|teflon tape|ptfe tape"),
    ("spiral wound gasket", r"spiral[\s-]*wound|\bspw\b|\bswg\b"),
    ("y-strainer", r"strainers?"),
    ("butterfly valve", r"butterfly|\bbfly\b|\bbfv\b"),
    ("swing check valve", r"swing[\s-]*(?:check|chk)"),
    ("check valve", r"\bcheck\b|\bchk\b"),
    ("ball valve", r"\bball\b"),
    ("gate valve", r"\bgate\b"),
    ("globe valve", r"\bglobe\b"),
    ("weld neck flange", r"weld[\s-]*neck|\bwnrf\b|\brfwn\b"),
    ("slip-on flange", r"slip[\s-]*on|\bsorf\b|\brfso\b"),
    ("blind flange", r"\bblind\b"),
    ("half coupling", r"half[\s-]*(?:couplings?|cplg)"),
    ("coupling", r"couplings?|\bcplgs?\b"),
    ("hex bushing", r"bushings?|\bbush\b"),
    ("street elbow", r"street[\s-]*(?:elbows?|ells?)|\bst\.? ells?\b"),
)
_AFTER_ELBOWS = (
    ("tee", r"\btees?\b"),
    ("union", r"\bunions?\b"),
    ("plug", r"\bplugs?\b"),
    ("cap", r"\bcaps?\b"),
    ("nipple", r"\bnipples?\b|\bnip\b"),
    ("pipe", r"\bpipe\b"),
)
# Parts that share a word with a stocked product but are a different thing.
_NOT_STOCKED = re.compile(r"spectacle|paddle blind|spade|plug valve|needle valve|relief valve|control valve|lap joint")


def _products(text: str) -> tuple:
    if _NOT_STOCKED.search(text):
        return ()
    for name, pattern in _PRODUCTS:
        if re.search(pattern, text):
            return (name,)
    if _FLANGE_WORD.search(text):  # short codes are only trusted next to the word "flange"
        for name, pattern in (("weld neck flange", r"\bwn\b"), ("slip-on flange", r"\bso\b"), ("blind flange", r"\bbld?\b")):
            if re.search(pattern, text):
                return (name,)
        return ()
    if _ELBOW_WORD.search(text):
        if re.search(r"\b45\b", text):
            return ("45 elbow",)
        if re.search(r"\b90\b", text):
            return ("90 elbow",)
        return ("90 elbow", "45 elbow")  # the angle was not stated: the customer has to say
    if re.search(r"\breducers?\b|\b(?:conc|ecc)\.?\s*red\b", text):
        if re.search(r"\becc", text):
            return ("eccentric reducer",)
        if re.search(r"\bconc", text):
            return ("concentric reducer",)
        return ("concentric reducer", "eccentric reducer")
    for name, pattern in _AFTER_ELBOWS:
        if re.search(pattern, text):
            return (name,)
    return ()


# --- size ---------------------------------------------------------------------------------

# Numbers that are not sizes. They are blanked out before sizes are looked for.
_NOT_A_SIZE = (
    r"\b\d+\s*(?:#|lbs?\b|wog\b|cwp\b|psi\b)", r"(?:\bclass|\bcl\.?)\s*\d+", r"#\s*\d+",
    r"\bsch(?:edule)?\.?\s*\d+s?\b", r"\b\d+\s*(?:ga|gauge)\b",
    r"\ba\s?\d{2,3}[a-z]?\b", r"\b(?:tp|wp|f|ss|type)\s?\d{3}l?\b", r"\b(?:304|316|317|321|347|410)l?\b",
    r"\bb7m?\b", r"\bb8m?\b", r"\b2h\b", r"\b8m\b", r"\bgr(?:ade)?\.?\s*\d+\b", r"\b13\s*%?\s*cr\b", r"\bcf8m?\b",
    r"\b(?:90|45)\b(?=[\s-]*(?:deg|d\b|lr\b|sr\b|elbow|ell|el\b|'?s\b))", r"(?:elbows?|ells?)\W{0,3}(?:90|45)\b",
    r"(?:w/|with)\s*\(?\d+\)?\s*(?:heavy\s*)?(?:hex\s*)?nuts?", r"\b\d+\s*(?:heavy\s*)?hex nuts?",
    r"\b\d+(?:\.\d+)?\s*(?:'|ft\b|feet\b|foot\b)", r"\bmodel[:#\s]+\S+", r"\bser(?:ial)?\.?\s*(?:no\.?|#)?[:\s]+\S+",
    r"\bp/?n[:#\s]+\S+", r"\bitem\s*#?\s*\d+", r"\b\d{5,}\b",
)  # fmt: skip
_THREAD_PITCH = (re.compile(r'"\s*-\s*\d{1,2}\b(?![/"\d])'), re.compile(r"(?<=\d/\d)-\d{1,2}\b(?![/\d])"))
_DIMENSION = re.compile(
    r'(?<![\w/.#-])(\d+(?:\.\d+)?)(?:[ -](\d+)/(\d+)|/(\d+))?(?![\w#])[\s-]*("|in\b\.?|inch(?:es)?\b)?'
)
_TIMES = re.compile(r"^\s*(?:x|by)\s*$")
_TWO_NUMBER_SIZES = {"stud bolt", "hex cap screw", "nipple"}  # diameter and length
_LARGE_END_FIRST = {"concentric reducer", "eccentric reducer", "hex bushing"}


def _size(text: str, products: tuple) -> tuple:
    text = _THREAD_PITCH[0].sub('"', text)
    text = _THREAD_PITCH[1].sub("", text)
    for pattern in _NOT_A_SIZE:
        text = re.sub(pattern, " ", text)
    found = []  # (value, start, end, has inch mark)
    for m in _DIMENSION.finditer(text):
        whole, numerator, denominator, simple_denominator, mark = m.groups()
        value = float(whole)
        if numerator:
            value += int(numerator) / int(denominator)
        elif simple_denominator:
            value = value / int(simple_denominator)
        found.append((value, m.start(), m.end(), bool(mark)))
    if not found:
        return ()
    product = products[0] if products else ""
    size = None
    for (a, _, a_end, _), (b, b_start, _, _) in zip(found, found[1:]):
        if _TIMES.match(text[a_end:b_start]):  # "6 x 4"
            size = (a, b)
            break
    if size is None and product in _TWO_NUMBER_SIZES and len(found) >= 2:
        size = (found[0][0], found[1][0])
    if size is None:
        marked = [f for f in found if f[3]]
        size = ((marked or found)[0][0],)
    if product == "nipple" and "close" in text:
        size = (size[0], "close")
    if product in _LARGE_END_FIRST and len(size) == 2:
        size = tuple(sorted(size, reverse=True))
    return size


def parse_catalog_size(value: str) -> tuple:
    """The catalog's own size column ("1-1/2", "6 x 4", "3/4 x close") in the same form."""
    out = []
    for piece in value.lower().split(" x "):
        piece = piece.strip()
        if not piece:
            continue
        if piece == "close":
            out.append("close")
            continue
        total = 0.0
        for part in piece.split("-"):
            total += int(part.split("/")[0]) / int(part.split("/")[1]) if "/" in part else float(part)
        out.append(total)
    return tuple(out)


# --- spec, material, ends, extras ---------------------------------------------------------


def _specs(text: str) -> frozenset:
    found = set()
    for pattern in (r"\b(\d{3,4})\s*(?:#|lbs?\b)", r"(?:\bclass|\bcl\.?)\s*(\d{3,4})\b", r"\b(\d{3,4})\s*class\b", r"#\s*(\d{3,4})\b"):
        found.update(f"{n}#" for n in re.findall(pattern, text))
    found.update(f"{n} WOG" for n in re.findall(r"\b(\d{3,4})\s*(?:wog|cwp)\b", text))
    found.update(f"SCH {n.upper()}" for n in re.findall(r"\bsch(?:edule)?\.?\s*(\d{2,3}s?)\b", text))
    found.update(f"{n} GA" for n in re.findall(r"\b(\d+)\s*(?:ga|gauge)\b", text))
    return frozenset(found)


def spec_kind(spec: str) -> str:
    if spec.startswith("SCH"):
        return "schedule"
    if spec.endswith("#") or spec.endswith("WOG"):
        return "rating"
    return "gauge" if spec.endswith("GA") else ""


# What each catalog material is, as facts a customer might state. The last rows are materials
# we do not stock: stating one of them can never match.
_MATERIAL_WORDS = (
    ("a105", r"\ba\s?105n?\b"), ("a106", r"\ba\s?106"), ("a53", r"\ba\s?53"), ("erw", r"\berw\b"),
    ("smls", r"\bsmls\b|seamless"), ("a234", r"\ba\s?234"), ("wpb", r"\bwpb\b"), ("a182", r"\ba\s?182"),
    ("a312", r"\ba\s?312"), ("a403", r"\ba\s?403"), ("a216", r"\ba\s?216"),
    ("316", r"\b(?:tp|wp|f|ss)?\s?316l?\b"), ("316l", r"316l\b"), ("304", r"\b(?:tp|wp|f|ss)?\s?304l?\b"), ("304l", r"304l\b"),
    ("ss", r"\bss\b|stainless|\bs/s\b|\bsst\b"), ("pre-galv", r"pre[\s-]*galv"), ("galv", r"galv|\bhdg\b"),
    ("mi", r"\bmi\b|malleable"), ("black", r"\bblack\b|\bblk\b"), ("bronze", r"\bbronze\b|\bbrz\b"),
    ("brass", r"\bbrass\b"), ("cast iron", r"cast iron|\bci\b"), ("wcb", r"\bwcb\b"),
    ("cs", r"carbon steel|\bcs\b|\bc\.s\.|\bcstl\b"), ("b7", r"\bb7\b"), ("b8m", r"\bb8m\b"), ("2h", r"\b2h\b"),
    ("8m", r"\b8m\b"), ("zinc", r"\bzinc\b"), ("gr5", r"\bgr(?:ade)?\.?\s*5\b"), ("graphite", r"graphite|grafoil"),
    ("cf8m", r"\bcf8m?\b"), ("ductile", r"ductile"), ("pvc", r"\bc?pvc\b"), ("copper", r"copper"),
    ("chrome-moly", r"chrome[\s-]?moly|\b(?:p|f|wp)(?:11|22|91)\b"), ("monel", r"monel"), ("hastelloy", r"hastelloy"),
    ("inconel", r"inconel"), ("duplex", r"duplex|\b2205\b|\b2507\b"), ("alloy 20", r"alloy\s*20"),
    ("aluminum", r"alumin"), ("titanium", r"titanium"), ("b7m", r"\bb7m\b"), ("b8", r"\bb8\b"),
    ("other stainless", r"\b(?:317|321|347|410)l?\b"), ("ptfe gasket", r"ptfe gasket|teflon gasket|rubber|neoprene|epdm|viton"),
)  # fmt: skip

CATALOG_MATERIALS = {
    "A105": {"a105", "cs"}, "A106B SMLS": {"a106", "smls", "cs", "black"}, "A53B ERW": {"a53", "erw", "cs", "black"},
    "A53 GALV": {"a53", "galv", "cs"}, "A234 WPB": {"a234", "wpb", "cs"}, "MI BLACK": {"mi", "black"},
    "MI GALV": {"mi", "galv"}, "304 SS": {"304", "ss"}, "316 SS": {"316", "ss"},
    "A182 F304L": {"a182", "304", "304l", "ss"}, "A182 F316L": {"a182", "316", "316l", "ss"},
    "A312 TP304L": {"a312", "304", "304l", "ss"}, "A312 TP316L": {"a312", "316", "316l", "ss"},
    "A403 WP304L": {"a403", "304", "304l", "ss"}, "A403 WP316L": {"a403", "316", "316l", "ss"},
    "304/GRAPHITE": {"304", "ss", "graphite"}, "316/GRAPHITE": {"316", "ss", "graphite"},
    "B7/2H": {"b7", "2h"}, "B8M/8M": {"b8m", "8m"}, "2H": {"2h"}, "BRASS": {"brass"}, "BRONZE": {"bronze"},
    "CAST IRON": {"cast iron"}, "WCB": {"wcb", "a216", "cs"}, "CARBON STEEL": {"cs"}, "GALV": {"galv"},
    "PRE-GALV": {"galv", "pre-galv"}, "ZINC": {"zinc"}, "GR5 ZINC": {"gr5", "zinc"},
}  # fmt: skip


def _material(text: str) -> frozenset:
    return frozenset(name for name, pattern in _MATERIAL_WORDS if re.search(pattern, text))


_THREADED = frozenset({"THD", "TBE", "T&C", "TOE"})
_END_WORDS = (  # (acceptable catalog end codes, pattern)
    (frozenset({"T&C"}), r"t\s*&\s*c\b|\bt/c\b|threaded (?:and|&) coupled|thd (?:and|&) cpld"),
    (frozenset({"TBE"}), r"\btbe\b|threaded both ends"),
    (frozenset({"TOE"}), r"\btoe\b|threaded one end"),
    (frozenset({"PE"}), r"\bpe\b|plain ends?"),
    (frozenset({"BE"}), r"bevel"),
    (frozenset({"BW"}), r"\bbw\b|butt[\s-]*weld|buttweld"),
    (frozenset({"SW"}), r"\bsw\b|socket[\s-]*weld|socketweld"),
    (frozenset({"RF"}), r"\brf\b|raised face|\bwnrf\b|\bsorf\b|\brfwn\b|\brfso\b"),
    (frozenset({"FF"}), r"\bff\b|flat face|full face"),
    (frozenset({"RTJ"}), r"\brtj\b|ring[\s-]*(?:type[\s-]*)?joint"),
    (frozenset({"RF", "FF", "RTJ"}), r"\bflanged\b|\bflgd\b"),
)
_THREADED_WORD = re.compile(r"\bthd\b|\bthrd\b|threaded\b|\b[fm]?npt\b|screwed|\bscrd\b")


def _ends(text: str, original: str) -> list:
    ends = [codes for codes, pattern in _END_WORDS if re.search(pattern, text)]
    if re.search(r"\bBE\b", original):  # upper case only: "be" is also an ordinary word
        ends.append(frozenset({"BE"}))
    specific_thread = any(codes < _THREADED for codes in ends)
    without_rod = re.sub(r"threaded rod|all[\s-]*thread", " ", text)
    if not specific_thread and _THREADED_WORD.search(without_rod):
        ends.append(_THREADED)
    return ends


_EXTRA_WORDS = (
    ("lr", r"\blr\b|long rad"), ("sr", r"\bsr\b|short rad"), ("13cr", r"\b13\s*%?\s*cr\b|13 chrome"),
    ("full port", r"full port|full bore|\bfp\b"), ("std port", r"std\.? port|standard port|reduced port|regular port"),
    ("locking handle", r"locking|lockable"), ("lug", r"\blug(?:ged)?\b"), ("wafer", r"\bwafer\b"),
    ("inner ring", r"inner ring"),
)  # fmt: skip


def _extras(text: str) -> frozenset:
    found = {name for name, pattern in _EXTRA_WORDS if re.search(pattern, text)}
    found.update(f"{n} ft" for n in re.findall(r"\b(\d+)\s*(?:ft|foot|feet)\b", text))
    return frozenset(found)


def parse_catalog_extra(value: str) -> frozenset:
    return frozenset(piece.strip().lower() for piece in value.split(",") if piece.strip())


def parse(description: str) -> Attributes:
    text = clean(description)
    products = _products(text)
    return Attributes(
        products=products,
        size=_size(text, products),
        specs=_specs(text),
        material=_material(text),
        ends=_ends(text, description),
        extras=_extras(text),
    )
