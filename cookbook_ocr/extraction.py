"""Ground structured recipe values in the OCR texts and their review findings."""

import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher

from .quantities import QUANTITY_PATTERN, quantity_matches


PAGE_MARKER = re.compile(r"^\[page: ([1-9][0-9]*)\][ \t]*\r?$", re.MULTILINE)
UNREADABLE = re.compile(r"\[(?:unleserlich|unreadable|illegible)\]", re.IGNORECASE)
_UNREADABLE_TOKEN = re.compile(rf"[\w./⁄∕]*{UNREADABLE.pattern}[\w./⁄∕]*", re.IGNORECASE)
INGREDIENT_FIELDS = ("name", "quantity", "unit", "preparation", "optional_when")
MEASURE_FIELDS = {"quantity", "servings", "duration"}
# A wrapped amount can span lines, but a blank line separates source paragraphs.
MEASURE_WHITESPACE = r"(?:[^\S\r\n]|\r?\n(?![^\S\r\n]*\r?\n))"
MEASURE_TOKEN = re.compile(QUANTITY_PATTERN.replace(r"\s", MEASURE_WHITESPACE), re.IGNORECASE)
PRINTED_PAGE_LABEL = re.compile(r"^\s*(?:page|seite|p[áa]gina)[ \t]+\d+\.?[ \t]*\r?\n", re.IGNORECASE)
INGREDIENT_BREAK = re.compile(r"[;,!?]|\.(?=\s)|\s+[-–—]\s+|\b(?:and|und|e)\b", re.IGNORECASE)
INGREDIENT_HEADING = re.compile(r"(?:ingredients?|ingredientes?|zutaten)\s*:?", re.IGNORECASE)
SECTION_HEADING = re.compile(
    r"(?:method|directions|instructions|preparation|modo de preparo|preparo|zubereitung)\s*:?", re.IGNORECASE,
)
MEASURED_LINE = re.compile(rf"\s*[-•]?\s*{QUANTITY_PATTERN}\s+[^\W\d_]", re.IGNORECASE)
TIME_OR_TEMPERATURE = re.compile(
    r"\s*(?:°\s*[CF]|seconds?|minutes?|hours?|secs?|mins?|hrs?|segundos?|minutos?|horas?|sekunden?|minuten?|stunden?)\b",
    re.IGNORECASE,
)
AMOUNT_UNIT_GAP = re.compile(r"\s*(?:(?:heaped|level|rasa?s?|cheia?s?|gestrichen(?:e[nr]?)?)\s+)?", re.I)


@dataclass(frozen=True)
class _Anchor:
    start: int
    end: int
    quote: re.Match


def flag_ocr_uncertainty(comparison: dict, a_pages: dict, b_pages: dict) -> dict:
    """Preserve explicit uncertainty even when the comparator drops a marker."""
    reason = "An OCR reader could not read part of this passage. Check the original document."

    def contains_marker(item):
        return any(UNREADABLE.search(item[key] or "") for key in ("a_excerpt", "b_excerpt"))

    reviews = [
        {**item, "kind": "uncertain", "reason": reason}
        if item["kind"] != "uncertain" and contains_marker(item) else item
        for item in comparison["review_items"]
    ]
    equivalents = []
    for item in comparison["equivalent_variations"]:
        if contains_marker(item):
            reviews.append({**item, "section": "Transcription", "kind": "uncertain", "reason": reason})
        else:
            equivalents.append(item)

    for side, other, pages, other_pages in (
        ("a_excerpt", "b_excerpt", a_pages, b_pages), ("b_excerpt", "a_excerpt", b_pages, a_pages),
    ):
        for page, body in pages.items():
            for line in body.splitlines():
                line = line.strip()
                markers = UNREADABLE.findall(line)
                if not markers:
                    continue
                if any(
                    item["kind"] == "uncertain" and (page is None or item["page"] in (None, page))
                    and any(len(UNREADABLE.findall(part)) >= len(markers) and (part in line or line in part)
                            for part in (item[side] or "").splitlines())
                    for item in reviews
                ):
                    continue
                parts = UNREADABLE.split(line)
                aligned = []
                if any(part.strip() for part in parts):
                    pattern = r".+?".join(re.escape(part) for part in parts)
                    aligned = [candidate.strip() for candidate in other_pages.get(page, "").splitlines()
                               if re.fullmatch(pattern, candidate.strip())]
                elif line in other_pages.get(page, "").splitlines():
                    aligned = [line]
                reviews.append({
                    "page": page, "section": "Transcription", "kind": "uncertain", "reason": reason,
                    side: line, other: aligned[0] if len(aligned) == 1 else None,
                })
    return {**comparison, "review_items": reviews, "equivalent_variations": equivalents}


def _occurrences(text: str, excerpt: str) -> list[int]:
    return [match.start() for match in re.finditer(re.escape(excerpt), text)]


def _source_quotes(text: str, quote: str) -> list[re.Match]:
    matches = list(re.finditer(re.escape(quote), text))
    if matches:
        return matches
    # Recover exact source whitespace when the model flattened a line break.
    pattern = r"\s+".join(re.escape(part) for part in quote.split())
    return list(re.finditer(pattern, text))


def _page_at(markers: list[re.Match], offset: int) -> str | None:
    return next(
        (marker.group(1) for marker in reversed(markers) if marker.end() <= offset),
        None,
    )


def _measure_cut_offsets(text: str, markers: list[re.Match]) -> set[int]:
    cuts = set()
    starts = [0, *(marker.end() for marker in markers)]
    ends = [*(marker.start() for marker in markers), len(text)]
    for start, end in zip(starts, ends):
        label = PRINTED_PAGE_LABEL.match(text[start:end])
        if label:
            start += label.end()
        for match in MEASURE_TOKEN.finditer(text, start, end):
            cuts.update(range(match.start() + 1, match.end()))
    return cuts


def _review_ranges(
    text: str, source: str, pages: list[re.Match], review_items: list[dict]
) -> list[tuple[int, int, int]]:
    ranges = []
    unreadable_tokens = [match.span() for match in _UNREADABLE_TOKEN.finditer(text)]
    for index, item in enumerate(review_items, start=1):
        excerpt = item[f"{source.lower()}_excerpt"]
        other = item["b_excerpt" if source == "A" else "a_excerpt"]
        if not excerpt:
            continue

        # Prefer the changed portion to surrounding words quoted for context.
        if other and excerpt != other:
            changes = [
                (start, end)
                for tag, start, end, _, _ in SequenceMatcher(
                    None, excerpt, other, autojunk=False
                ).get_opcodes()
                if tag != "equal"
            ]
        else:
            changes = [(0, len(excerpt))]

        for offset in _occurrences(text, excerpt):
            page = _page_at(pages, offset)
            if item["page"] is None or page is None or page == item["page"]:
                ranges.extend((offset + start, offset + end, index) for start, end in changes)
                if item.get("kind") == "uncertain":
                    # Adjacent letters or digits belong to the same unreadable token.
                    ranges.extend(
                        (start, end, index) for start, end in unreadable_tokens
                        if offset < end and start < offset + len(excerpt)
                    )
    return ranges


def _field(span, field: str, transcriptions, pages, review_ranges, measure_cuts, anchors: list) -> dict:
    if span is None:
        return {"value": None, "status": "missing", "evidence": None, "review_item_ids": []}

    source, quote, value = span["source"], span["quote"], span["text"]
    if source not in transcriptions or not quote.strip() or not value.strip():
        raise ValueError(f"Invalid evidence for {field}.")
    text = transcriptions[source]
    if not _occurrences(quote, value):
        raise ValueError(f"Extraction returned non-verbatim evidence for {field}.")
    quotes = [
        match for match in _source_quotes(text, quote)
        if not any(match.start() < marker.end() and marker.start() < match.end()
                   for marker in pages[source])
    ]
    spans = [
        _Anchor(source_quote.start() + match.start(), source_quote.start() + match.end(), source_quote)
        for source_quote in quotes for match in _source_quotes(source_quote.group(), value)
    ]
    if field in MEASURE_FIELDS:
        # An ambiguous quote must not select a separate measure over the intended mixed number.
        cuts = measure_cuts[source]
        if any(
            anchor.start in cuts or anchor.end in cuts
            or re.fullmatch(r"[\d.,/⁄∕]", text[max(0, anchor.start - 1):anchor.start])
            or re.match(r"[\d/⁄∕]|[.,]\d", text[anchor.end:anchor.end + 2])
            for anchor in spans
        ):
            raise ValueError(f"Extraction returned ambiguous evidence or a partial measure for {field}.")
    if not spans:
        raise ValueError(f"Extraction returned non-verbatim evidence or a partial measure for {field}.")

    anchors.extend(spans)
    return _anchored_field(value, source, text, pages[source], review_ranges[source], spans)


def _anchored_field(value, source, text, pages, review_ranges, anchors) -> dict:
    page_numbers = {_page_at(pages, anchor.start) for anchor in anchors}
    page = page_numbers.pop() if len(page_numbers) == 1 else None
    review_ids = set()
    for anchor in anchors:
        review_ids.update(
            index for left, right, index in review_ranges
            if (anchor.start < right and left < anchor.end)
            or (left == right and anchor.start <= left <= anchor.end)
        )

    needs_review = bool(review_ids or UNREADABLE.search(value))
    anchor = anchors[0]
    return {
        "value": None if needs_review else text[anchor.start:anchor.end],
        "status": "needs_review" if needs_review else "stated",
        "evidence": {"source": source, "page": page, "quote": anchor.quote.group()},
        "review_item_ids": sorted(review_ids),
    }


def _name_mentions(value: str) -> re.Pattern:
    # Ignore OCR hyphenation and spacing without changing the evidence we return.
    words = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", value).split()
    parts = [r"(?:-\s*\n\s*)?".join(re.escape(char) for char in word) for word in words]
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", re.IGNORECASE)


def _ingredient_anchor(text, anchor, name, others, name_anchors):
    line_start = text.rfind("\n", 0, anchor.start) + 1
    line_end = text.find("\n", anchor.end)
    if line_end == -1:
        line_end = len(text)
    # A quote that explicitly spans a wrapped line can supply the same ingredient occurrence.
    start = min(line_start, anchor.quote.start())
    end = max(line_end, anchor.quote.end())
    other_mentions = [mention.span() for other in others for mention in other.finditer(text, start, end)]
    for mention in name.finditer(text, start, end):
        if name_anchors is not None and mention.span() not in name_anchors:
            continue
        if any(left <= mention.start() and mention.end() <= right for left, right in other_mentions):
            continue
        if mention.end() <= anchor.start:
            gap = text[mention.end():anchor.start]
            previous_breaks = list(INGREDIENT_BREAK.finditer(text, start, mention.start()))
            local_start = previous_breaks[-1].end() if previous_breaks else start
            if not re.match(r"\s*[:(]", gap) and MEASURE_TOKEN.search(text, local_start, mention.start()):
                continue
        elif mention.start() >= anchor.end:
            gap = text[anchor.end:mention.start()]
        else:
            continue
        if (len(gap) <= 64 and gap.count("\n") <= 1 and not re.search(r"\n\s*\n", gap)
                and not INGREDIENT_BREAK.search(gap) and not MEASURE_TOKEN.search(gap)
                and not any(other.search(gap) for other in others)):
            return True
    return False


def _unit_anchors(quantity_value, quantity_source, unit_source, unit_anchors, quantity_anchors, transcriptions):
    text = transcriptions[unit_source]
    if quantity_source == unit_source:
        return [unit for unit in unit_anchors if any(
            quantity.end <= unit.start and AMOUNT_UNIT_GAP.fullmatch(text[quantity.end:unit.start])
            for quantity in quantity_anchors
        )]

    # The local amount in the other reading can use an equivalent fraction notation.
    compatible = []
    for unit in unit_anchors:
        start = text.rfind("\n", 0, unit.start) + 1
        candidates = list(MEASURE_TOKEN.finditer(text, min(start, unit.quote.start()), unit.start))
        if candidates and AMOUNT_UNIT_GAP.fullmatch(text[candidates[-1].end():unit.start]):
            amount = candidates[-1].group()
            if amount == quantity_value or quantity_matches(amount, None, quantity_value, None):
                compatible.append(unit)
    return compatible


def _deduplicate_entities(items, anchors):
    unique = []
    aliases = {}
    indexes = {}
    seen = {}
    for index, (item, occurrence) in enumerate(zip(items, anchors)):
        fields = [field for name, field in item.items() if name != "id"]
        key = (occurrence, tuple((field["value"], field["status"]) for field in fields))
        if occurrence is not None and all(field["status"] != "needs_review" for field in fields) and key in seen:
            indexes[index], aliases[item["id"]] = seen[key]
        else:
            indexes[index] = len(unique)
            seen[key] = len(unique), item["id"]
            unique.append(item)
    return unique, aliases, indexes


def _omitted_ingredient_lines(transcriptions, names):
    """Report agreed measured lines under explicit ingredient headings."""
    def measured_lines(source):
        in_ingredients = False
        page = None
        for line in transcriptions[source].splitlines():
            if marker := PAGE_MARKER.fullmatch(line):
                page = marker[1]
                in_ingredients = False
            elif INGREDIENT_HEADING.fullmatch(line.strip()):
                in_ingredients = True
            elif not line.strip() or SECTION_HEADING.fullmatch(line.strip()):
                in_ingredients = False
            elif in_ingredients and MEASURED_LINE.match(line) and not UNREADABLE.search(line):
                yield page, line.strip()

    second_reading = set(measured_lines("B"))
    for page, line in dict.fromkeys(measured_lines("A")):
        if (page, line) in second_reading and not any(name.search(line) for name in names):
            yield {"source": "A", "page": page, "quote": line}


def build_recipe(
    candidate: dict, transcriptions: dict[str, str], review_items: list[dict],
    *, extraction_issues: list[dict] | None = None,
) -> dict:
    pages = {source: list(PAGE_MARKER.finditer(text)) for source, text in transcriptions.items()}
    ranges = {
        source: _review_ranges(text, source, pages[source], review_items)
        for source, text in transcriptions.items()
    }
    measure_cuts = {
        source: _measure_cut_offsets(text, pages[source]) for source, text in transcriptions.items()
    }

    def field(record, name, path, anchors=None):
        try:
            return _field(record[name], name, transcriptions, pages, ranges, measure_cuts,
                          anchors if anchors is not None else [])
        except ValueError as error:
            if extraction_issues is None:
                raise
            extraction_issues.append({"field": f"{path}/{name}", "reason": str(error)})
            return {"value": None, "status": "needs_review", "evidence": None, "review_item_ids": []}

    def resolve_ingredient(index, item, names):
        path = f"/ingredients/{index}"
        anchors = defaultdict(list)
        ingredient = {"id": item["id"], **{
            name: field(item, name, path, anchors[name]) for name in INGREDIENT_FIELDS
        }}
        others = [name for name in names if name.pattern != names[index].pattern]
        name_spans = {(anchor.start, anchor.end) for anchor in anchors["name"]}
        related = {}

        def flag(key, reason):
            if extraction_issues is None:
                raise ValueError(reason)
            extraction_issues.append({"field": f"{path}/{key}", "reason": reason})
            ingredient[key].update(value=None, status="needs_review")

        for key in ("quantity", "unit"):
            if not anchors[key]:
                continue
            source = item[key]["source"]
            text = transcriptions[source]
            allowed = name_spans if source == item["name"]["source"] else None
            linked = [anchor for anchor in anchors[key]
                      if _ingredient_anchor(text, anchor, names[index], others, allowed)
                      and not (key == "quantity" and TIME_OR_TEMPERATURE.match(text, anchor.end))]
            if linked:
                ingredient[key] = _anchored_field(item[key]["text"], source, text, pages[source], ranges[source], linked)
                related[key] = linked
            else:
                flag(key, f"Extraction could not tie the {key} to the same ingredient in the source.")

        # Independently found quantity and unit must describe the same local measure.
        if "quantity" in related and "unit" in related and ingredient["quantity"]["status"] == "stated":
            source = item["unit"]["source"]
            compatible = _unit_anchors(
                item["quantity"]["text"], item["quantity"]["source"], source,
                related["unit"], related["quantity"], transcriptions,
            )
            if compatible:
                ingredient["unit"] = _anchored_field(item["unit"]["text"], source, transcriptions[source],
                                                     pages[source], ranges[source], compatible)
            else:
                flag("unit", "Extraction could not tie the unit to the same ingredient amount in the source.")
        occurrence = None
        if len(anchors["name"]) == 1:
            anchor = anchors["name"][0]
            occurrence = (item["name"]["source"], anchor.start, anchor.end)
            if "quantity" in related:
                if len(related["quantity"]) == 1:
                    amount = related["quantity"][0]
                    occurrence += (item["quantity"]["source"], amount.start, amount.end)
                else:
                    occurrence = None
        return ingredient, occurrence

    names = [_name_mentions(item["name"]["text"]) for item in candidate["ingredients"]]
    ingredients, ingredient_anchors = [], []
    for index, item in enumerate(candidate["ingredients"]):
        ingredient, occurrence = resolve_ingredient(index, item, names)
        ingredients.append(ingredient)
        ingredient_anchors.append(occurrence)
    tools, tool_anchors = [], []
    for index, item in enumerate(candidate["tools"]):
        anchors = []
        tools.append({"id": item["id"], "name": field(item, "name", f"/tools/{index}", anchors)})
        tool_anchors.append((item["name"]["source"], anchors[0].start, anchors[0].end) if len(anchors) == 1 else None)
    ingredient_ids = {item["id"] for item in ingredients}
    tool_ids = {item["id"] for item in tools}
    if len(ingredient_ids) != len(ingredients) or len(tool_ids) != len(tools):
        raise ValueError("Extraction returned duplicate ingredient or tool IDs.")
    if any(not key.strip() for key in ingredient_ids | tool_ids):
        raise ValueError("Extraction returned an empty ingredient or tool ID.")
    ingredients, ingredient_aliases, ingredient_indexes = _deduplicate_entities(ingredients, ingredient_anchors)
    tools, tool_aliases, tool_indexes = _deduplicate_entities(tools, tool_anchors)
    if extraction_issues is not None:
        indexes = {"ingredients": ingredient_indexes, "tools": tool_indexes}
        for issue in extraction_issues:
            match = re.fullmatch(r"/(ingredients|tools)/(\d+)/(\w+)", issue["field"])
            if match:
                collection, index, name = match.groups()
                if int(index) in indexes[collection]:
                    issue["field"] = f"/{collection}/{indexes[collection][int(index)]}/{name}"

    for evidence in _omitted_ingredient_lines(transcriptions, names):
        reason = "A measured ingredient line agreed on by both OCR readings is missing from the extraction."
        if extraction_issues is None:
            raise ValueError(reason)
        extraction_issues.append({"field": "/ingredients", "reason": reason, "evidence": evidence})

    steps = []
    for index, step in enumerate(candidate["steps"]):
        if set(step["ingredient_ids"]) - ingredient_ids or set(step["tool_ids"]) - tool_ids:
            raise ValueError("Extraction returned a step reference to an unknown ingredient or tool.")
        steps.append({
            **{
                name: field(step, name, f"/steps/{index}")
                for name in ("instruction", "duration", "condition")
            },
            "ingredient_ids": list(dict.fromkeys(ingredient_aliases.get(key, key) for key in step["ingredient_ids"])),
            "tool_ids": list(dict.fromkeys(tool_aliases.get(key, key) for key in step["tool_ids"])),
        })

    return {
        "title": field(candidate, "title", ""),
        "servings": field(candidate, "servings", ""),
        "ingredients": ingredients,
        "tools": tools,
        "steps": steps,
    }


def has_unresolved_fields(recipe: dict) -> bool:
    fields = [recipe["title"], recipe["servings"]]
    fields.extend(item[name] for item in recipe["ingredients"] for name in INGREDIENT_FIELDS)
    fields.extend(item["name"] for item in recipe["tools"])
    fields.extend(step[name] for step in recipe["steps"] for name in ("instruction", "duration", "condition"))
    return any(field["status"] == "needs_review" for field in fields)
