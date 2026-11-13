"""Compare explicit limits and negations in masked recipe text."""

import re
import unicodedata


MEASURE_TOKEN = re.compile(r"\[MEASURE_[A-Z]+\]", re.IGNORECASE)

# Recognize common English, German and Portuguese ways to express a limit.
# The input is case-folded and accents are removed before matching.
_BOUND_PHRASES = {
    "<=": (r"at most|(?:no|not) (?:more|longer|higher) than|up to|maximum(?: of)?|max\.?|"
           r"(?:do not|don't|never) exceed|hochstens|maximal|nicht (?:mehr|langer|hoher) als|bis zu|"
           r"no maximo|ao maximo|maximo(?: de)?|nao mais (?:de|que)|ate|nao ultrapass[ea]r?|≤|<="),
    ">=": (r"at least|(?:no|not) (?:less|shorter|lower) than|minimum(?: of)?|"
           r"mindestens|minimal|nicht (?:weniger|kurzer|niedriger) als|"
           r"no minimo|ao menos|pelo menos|minimo(?: de)?|nao menos (?:de|que)|≥|>="),
    "<": (r"less than|fewer than|under|(?:shorter|lower) than|before(?: the)?|"
          r"(?:weniger|kurzer|niedriger) als|unter|vor(?: dem| der)?|menos (?:de|que)|abaixo de|inferior a|antes de|<"),
    ">": (r"more than|over|(?:longer|higher|greater) than|"
          r"after(?: the)?|(?:mehr|langer|hoher) als|uber|nach(?: dem| der)?|"
          r"mais (?:de|que)|acima de|superior a|(?:depois|apos)(?: de)?|>"),
}
_PREFIX_BOUND = re.compile(
    r"(?<!\w)(?:" + "|".join(f"(?P<bound_{i}>{phrase})" for i, phrase in enumerate(_BOUND_PHRASES.values()))
    + r")\s*[:=]?\s*$"
)
_BOUND_TYPES = tuple(_BOUND_PHRASES)
_SUFFIX_BOUND = re.compile(
    r"\s*(?:[a-z°%]+\s+)?(?:"
    r"(?P<upper>or (?:less|fewer)|oder weniger|ou menos|at most|maximum|hochstens|no maximo|"
    r"nicht uberschreiten)|"
    r"(?P<lower>or more|oder mehr|ou mais|at least|minimum|mindestens|no minimo))\b"
)
_NEGATION = re.compile(
    r"\b(?:not(?!\s+(?:only|just)\b)|never|without|cannot|[a-z]+n't|uncovered|"
    r"no(?=\s+(?:added|stirring|salt|sugar|oil|butter|flour|milk|water)\b)|"
    r"nicht(?!\s+nur\b)|nie|niemals|ohne|kein(?:e[nmrs]?)?|unbedeckt|ungedeckt|"
    r"nao(?!\s+(?:so|apenas)\b)|nunca|jamais|sem|nenhum(?:a|as|s)?|destampad[oa]s?)\b"
)
_CLAUSE_BREAK = re.compile(r"(?<!\bmax)\.|[!?;,]|\b(?:then|but|dann|aber|depois|mas|em seguida)\b")
_CONDITIONAL = re.compile(r"\b(?:if|when|wenn|falls|se|quando)\b")
_COMPLEMENT = {"<": ">=", ">": "<=", "<=": ">", ">=": "<"}
_RANGE_PREFIX = re.compile(r"\b(between|from|entre|de|zwischen|von)\s*$")
_RANGE_LINKS = {
    "between": {"and"}, "from": {"to"}, "entre": {"e"},
    "de": {"a", "ate"}, "zwischen": {"und"}, "von": {"bis"},
}
_COORDINATION = re.compile(r"\b(?:and|or|und|oder|e|ou)\b")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold().replace("’", "'"))
    return " ".join("".join(char for char in text if not unicodedata.combining(char)).split())


def _constraints(text: str) -> tuple[list[str | None], list[int], int]:
    """Associate comparisons with their measures and retain other clause negations."""
    text = _normalize(text)
    bounds = []
    negations_by_clause = []
    conditional_negations = 0
    for clause in _CLAUSE_BREAK.split(text):
        if not clause.strip():
            continue
        negations = list(_NEGATION.finditer(clause))
        consumed = set()
        previous_end = 0
        range_links = set()
        coordinations = list(_COORDINATION.finditer(clause))
        for measure in MEASURE_TOKEN.finditer(clause):
            prefix = _PREFIX_BOUND.search(clause, previous_end, measure.start())
            suffix = _SUFFIX_BOUND.match(clause, measure.end())
            link = clause[previous_end:measure.start()].strip()
            if link in range_links:
                bounds[-1] = ">="
                bound = "<="
                coordinations = [separator for separator in coordinations
                                 if not previous_end <= separator.start() < measure.start()]
            elif prefix:
                index = int(prefix.lastgroup.removeprefix("bound_"))
                bound = _BOUND_TYPES[index]
                bound_start = max([previous_end, *(separator.end() for separator in coordinations
                                                   if previous_end <= separator.end() <= prefix.start())])
                for negation in negations:
                    if prefix.start() <= negation.start() < measure.start():
                        # The wording of this comparison already includes its negation.
                        consumed.add(negation.start())
                preceding = [negation for negation in negations if (
                    bound_start <= negation.start() < prefix.start() and negation.start() not in consumed
                )]
                if preceding:
                    # “Not until after” keeps the lower limit expressed by “after”.
                    until_after = (prefix.group().startswith("after ") and re.search(
                        r"\buntil\s*$", clause[preceding[-1].end():prefix.start()],
                    ))
                    if not until_after:
                        bound = _COMPLEMENT[bound]
                    consumed.add(preceding[-1].start())
            elif suffix:
                bound = "<=" if suffix.lastgroup == "upper" else ">="
                consumed.update(negation.start() for negation in negations
                                if suffix.start() <= negation.start() < suffix.end())
                coordinations = [separator for separator in coordinations
                                 if not suffix.start() <= separator.start() < suffix.end()]
            else:
                bound = None
            bounds.append(bound)
            range_start = _RANGE_PREFIX.search(clause, previous_end, measure.start())
            range_links = _RANGE_LINKS[range_start[1]] if range_start else set()
            previous_end = measure.end()
        positions = [negation.start() for negation in negations if negation.start() not in consumed]
        if condition := _CONDITIONAL.search(clause):
            conditional_negations += sum(position >= condition.end() for position in positions)
            positions = [position for position in positions if position < condition.end()]
        ends = [separator.start() for separator in coordinations] + [len(clause)]
        start = 0
        for end in ends:
            negations_by_clause.append(sum(start <= position < end for position in positions))
            start = end
    return bounds, negations_by_clause, conditional_negations


def preserves_constraints(source: str, translation: str) -> bool:
    """Check common limit equivalences and explicit negation across languages."""
    original_bounds, original_negations, original_conditionals = _constraints(source)
    translated_bounds, translated_negations, translated_conditionals = _constraints(translation)
    if original_bounds != translated_bounds or original_conditionals != translated_conditionals:
        return False
    if not any(original_negations) and not any(translated_negations):
        return True
    return original_negations == translated_negations
