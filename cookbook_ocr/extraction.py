"""Ground structured recipe values in the OCR texts and their review findings."""

import re
from difflib import SequenceMatcher

from .quantities import QUANTITY_PATTERN


PAGE_MARKER = re.compile(r"^\[page: ([1-9][0-9]*)\][ \t]*\r?$", re.MULTILINE)
UNREADABLE = re.compile(r"\[(?:unleserlich|unreadable|illegible)\]", re.IGNORECASE)
INGREDIENT_FIELDS = ("name", "quantity", "unit", "preparation", "optional_when")
MEASURE_FIELDS = {"quantity", "servings", "duration"}
# A wrapped amount can span lines, but a blank line separates source paragraphs.
MEASURE_WHITESPACE = r"(?:[^\S\r\n]|\r?\n(?![^\S\r\n]*\r?\n))"
MEASURE_TOKEN = re.compile(QUANTITY_PATTERN.replace(r"\s", MEASURE_WHITESPACE), re.IGNORECASE)
PRINTED_PAGE_LABEL = re.compile(r"^\s*(?:page|seite|p[áa]gina)[ \t]+\d+\.?[ \t]*\r?\n", re.IGNORECASE)


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
    return ranges


def _field(span, field: str, transcriptions, pages, review_ranges, measure_cuts) -> dict:
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
        (source_quote.start() + match.start(), source_quote.start() + match.end(), source_quote)
        for source_quote in quotes for match in _source_quotes(source_quote.group(), value)
    ]
    if field in MEASURE_FIELDS:
        # An ambiguous quote must not select a separate measure over the intended mixed number.
        cuts = measure_cuts[source]
        if any(
            start in cuts or end in cuts
            or re.fullmatch(r"[\d.,/⁄∕]", text[max(0, start - 1):start])
            or re.match(r"[\d/⁄∕]|[.,]\d", text[end:end + 2])
            for start, end, _ in spans
        ):
            raise ValueError(f"Extraction returned ambiguous evidence or a partial measure for {field}.")
    if not spans:
        raise ValueError(f"Extraction returned non-verbatim evidence or a partial measure for {field}.")

    page_numbers = {_page_at(pages[source], start) for start, _, _ in spans}
    page = page_numbers.pop() if len(page_numbers) == 1 else None
    review_ids = set()
    for start, end, _ in spans:
        review_ids.update(
            index for left, right, index in review_ranges[source]
            if (start < right and left < end)
            or (left == right and start <= left <= end)
        )

    needs_review = bool(review_ids or UNREADABLE.search(value))
    start, end, source_quote = spans[0]
    return {
        "value": None if needs_review else text[start:end],
        "status": "needs_review" if needs_review else "stated",
        "evidence": {"source": source, "page": page, "quote": source_quote.group()},
        "review_item_ids": sorted(review_ids),
    }


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

    def field(record, name, path):
        try:
            return _field(record[name], name, transcriptions, pages, ranges, measure_cuts)
        except ValueError as error:
            if extraction_issues is None:
                raise
            extraction_issues.append({"field": f"{path}/{name}", "reason": str(error)})
            return {"value": None, "status": "needs_review", "evidence": None, "review_item_ids": []}

    ingredients = [
        {"id": item["id"], **{
            name: field(item, name, f"/ingredients/{index}") for name in INGREDIENT_FIELDS
        }}
        for index, item in enumerate(candidate["ingredients"])
    ]
    tools = [
        {"id": item["id"], "name": field(item, "name", f"/tools/{index}")}
        for index, item in enumerate(candidate["tools"])
    ]
    ingredient_ids = {item["id"] for item in ingredients}
    tool_ids = {item["id"] for item in tools}
    if len(ingredient_ids) != len(ingredients) or len(tool_ids) != len(tools):
        raise ValueError("Extraction returned duplicate ingredient or tool IDs.")
    if any(not key.strip() for key in ingredient_ids | tool_ids):
        raise ValueError("Extraction returned an empty ingredient or tool ID.")

    steps = []
    for index, step in enumerate(candidate["steps"]):
        if set(step["ingredient_ids"]) - ingredient_ids or set(step["tool_ids"]) - tool_ids:
            raise ValueError("Extraction returned a step reference to an unknown ingredient or tool.")
        steps.append({
            **{
                name: field(step, name, f"/steps/{index}")
                for name in ("instruction", "duration", "condition")
            },
            "ingredient_ids": list(dict.fromkeys(step["ingredient_ids"])),
            "tool_ids": list(dict.fromkeys(step["tool_ids"])),
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
