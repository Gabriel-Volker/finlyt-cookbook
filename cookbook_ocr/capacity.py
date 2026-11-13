"""Flag cooking vessels that may need a different size or more batches."""

import re
from collections.abc import Callable
from fractions import Fraction

from .quantities import NUMBER_PATTERN


VESSEL = re.compile(
    r"\b(?:"
    r"pans?(?![- ](?:fr(?:y|ied|ying)|sear\w*|juices?|drippings?|sauce)\b)"
    r"|saucepans?|skillets?|pots?|stockpots?|woks?|dutch ovens?|casserole dishes?|baking dishes?"
    r"|baking (?:trays?|sheets?)|cake tins?|loaf tins?"
    r"|(?:koch|brat|schmor|schnellkoch)?t(?:opf|öpfe|öpfen)"
    r"|(?:brat|schmor|back)?pfannen?|backformen?|auflaufformen?|backblech(?:e|en)?|bräter"
    r"|panelas?|frigideiras?|caçarolas?|assadeiras?|travessas?"
    r")\b", re.IGNORECASE,
)
SHORT_FORM = re.compile(
    r"\b(?:(?:in (?:die|der|eine|einer) )form|(?:na|numa|uma|a) formas?)\b", re.IGNORECASE
)

VOLUME = r"(?:ml|millilit(?:er|re)s?|mililitros?|l|lit(?:er|re|ro)s?|qt|quarts?)"
DIMENSION = r"(?:mm|cm|centimet(?:er|re|ro)s?|inches?|inch|in|[″\"])"
SIZE = (
    rf"(?P<volume>{NUMBER_PATTERN}[\s-]*{VOLUME})(?![\w])"
    rf"|(?P<dimension>{NUMBER_PATTERN}(?:\s*[x×]\s*{NUMBER_PATTERN}){{0,2}}"
    rf"[\s-]*{DIMENSION})(?![\w])"
)
SIZE_BEFORE_VESSEL = re.compile(
    rf"(?:{SIZE})(?:\s+|[-–]\s*)(?:(?:round|square|deep|large|wide)\s+){{0,2}}$",
    re.IGNORECASE,
)
SIZE_AFTER_VESSEL = re.compile(
    rf"^\s*(?:\(\s*|(?:de|von|of|with a capacity of|mit einem fassungsvermögen von)\s+)"
    rf"(?:{SIZE})(?=\s*(?:\)|\b(?:capacity|fassungsvermögen|de capacidade|and|e|und)\b|[.,;:]|$))",
    re.IGNORECASE,
)
KEEP_BATCH_SIZE = re.compile(
    r"\b(?:"
    r"same (?:size|sized) batches|same-sized batches|(?:keep|maintain) the original batch size"
    r"|lotes do mesmo tamanho|mesmo tamanho (?:de|dos) lotes"
    r"|gleich große[nr]? portionen|gleich große[nr]? chargen|ursprüngliche chargengröße"
    r")\b", re.IGNORECASE,
)
BATCH_CLAUSE_BOUNDARY = re.compile(r"[.!?;\n]+|,\s+(?:then|depois|dann)\b", re.IGNORECASE)


def _vessels(text: str) -> list[re.Match]:
    return sorted([*VESSEL.finditer(text), *SHORT_FORM.finditer(text)], key=lambda match: match.start())


def _stated_size(text: str, vessel: re.Match) -> tuple[str, str] | None:
    before = text[:vessel.start()]
    after = text[vessel.end():]
    size = SIZE_BEFORE_VESSEL.search(before) or SIZE_AFTER_VESSEL.match(after)
    if size:
        group = size.lastgroup
        return group, " ".join(size[group].split())
    return None


def _vessel_name(mention: re.Match) -> str:
    return mention.group().split()[-1].casefold() if mention.re is SHORT_FORM else mention.group().casefold()


def _keeps_vessel_batch_size(instruction: str, names: set[str]) -> bool:
    # Suppress only when the batching instruction clearly identifies this vessel.
    clauses = BATCH_CLAUSE_BOUNDARY.split(instruction)
    vessel_clauses = [
        (clause, mentions) for clause in clauses
        if names.intersection(mentions := {_vessel_name(m) for m in _vessels(clause)})
    ]
    return bool(vessel_clauses) and all(
        mentions <= names and KEEP_BATCH_SIZE.search(clause)
        for clause, mentions in vessel_clauses
    )


VESSEL_CAPACITY_WARNING = "Check whether this cooking vessel fits the larger batch or cook it in batches."
VESSEL_SIZE_WARNING = "Check whether the larger batch fits the vessel with stated {description} ({value}) or cook it in batches."
VESSEL_DISPLAY_TEXTS = (
    VESSEL_CAPACITY_WARNING,
    VESSEL_SIZE_WARNING.format(description="capacity", value="2 L"),
    VESSEL_SIZE_WARNING.format(description="dimensions", value="20 cm"),
)


def _reason(size: tuple[str, str] | None) -> str:
    if size is None:
        return VESSEL_CAPACITY_WARNING
    kind, value = size
    description = "capacity" if kind == "volume" else "dimensions"
    return VESSEL_SIZE_WARNING.format(description=description, value=value)


def review_vessel_capacity(
    recipe: dict, factor: Fraction, warn: Callable[[str, str], None],
) -> None:
    """Flag larger batches using the original tool names and cooking instructions."""
    if factor <= 1:
        return

    steps = recipe["steps"]
    reported_names = set()
    for index, tool in enumerate(recipe["tools"]):
        name_field = tool["name"]
        name = name_field["value"] or ""
        names = {_vessel_name(mention) for mention in _vessels(name)}
        if name.casefold() in {"forma", "form"}:
            names.add(name.casefold())
        if not names:
            continue
        # A source step can name the vessel even when extraction omits the link.
        mentions = [
            step["instruction"]["value"] or "" for step in steps
            if tool["id"] in step["tool_ids"] or any(
                _vessel_name(mention) in names for mention in _vessels(step["instruction"]["value"] or "")
            )
        ]
        if mentions and all(_keeps_vessel_batch_size(instruction, names) for instruction in mentions):
            reported_names.update(names)
            continue
        quote = (name_field.get("evidence") or {}).get("quote") or ""
        size = next((
            size for text in (name, quote, *mentions)
            for mention in _vessels(text)
            if _vessel_name(mention) in names and (size := _stated_size(text, mention))
        ), None)
        warn(f"/tools/{index}/name", _reason(size))
        reported_names.update(names)

    for index, step in enumerate(steps):
        instruction = step["instruction"]["value"] or ""
        mentions = [
            mention for mention in _vessels(instruction)
            if (name := _vessel_name(mention)) not in reported_names
            and not _keeps_vessel_batch_size(instruction, {name})
        ]
        if mentions:
            size = next((
                size for mention in mentions if (size := _stated_size(instruction, mention))
            ), None)
            warn(f"/steps/{index}/instruction", _reason(size))
