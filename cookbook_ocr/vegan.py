"""Apply explicit ingredient and cooking rules for the vegan adaptation."""

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Substitution:
    terms: str
    replacement: str
    reason: str
    step_replacement: str | None = None
    check_cooking: bool = False


SUBSTITUTIONS = (
    Substitution(r"Bratenstücke|Rinderbraten|Rindfleisch|Braten", "Seitan",
                 "Use a firm plant protein in place of the roast.", check_cooking=True),
    Substitution(r"pot roast|beef roast|beef chunks|beef(?!\s+drippings\b)", "seitan",
                 "Use a firm plant protein in place of beef.", check_cooking=True),
    Substitution(r"carne bovina|carne de boi|assado bovino", "seitan",
                 "Use a firm plant protein in place of beef.", check_cooking=True),
    Substitution(r"Hähnchenbrust|Hähnchenfleisch|Hähnchen", "fester Tofu",
                 "Use firm tofu in place of chicken.", check_cooking=True),
    Substitution(r"chicken breasts?|chicken", "firm tofu",
                 "Use firm tofu in place of chicken.", check_cooking=True),
    Substitution(r"peitos? de frango|frango", "tofu firme",
                 "Use firm tofu in place of chicken.", check_cooking=True),
    Substitution(r"Speck|Räucherspeck|Schinkenspeck", "Räuchertofu",
                 "Use smoked tofu for the smoky flavour of bacon."),
    Substitution(r"bacon strips?|bacon", "smoked tofu",
                 "Use smoked tofu for the smoky flavour of bacon."),
    Substitution(r"toucinho", "tofu defumado", "Use smoked tofu in place of bacon."),
    Substitution(r"(?:unsalted|salted) butter|butter", "vegan butter",
                 "Use a plant-based fat in place of dairy butter."),
    Substitution(r"manteiga", "manteiga vegana", "Use a plant-based fat in place of dairy butter."),
    Substitution(r"Kuhmilch|Vollmilch|Milch", "ungesüßter Sojadrink", "Use unsweetened soy milk."),
    Substitution(r"whole milk|(?:cow|goat|sheep)'?s? milk|milk", "unsweetened soy milk",
                 "Use unsweetened soy milk."),
    Substitution(r"leite de vaca|leite integral|leite", "leite de soja sem açúcar",
                 "Use unsweetened soy milk."),
    Substitution(r"Schlagsahne|Sahne", "Hafercreme", "Use oat cream in place of dairy cream."),
    Substitution(r"cream cheese", "vegan cream cheese", "Use plant-based cream cheese."),
    Substitution(r"heavy cream|double cream|cream(?!\s+cheese\b)", "oat cream",
                 "Use oat cream in place of dairy cream."),
    Substitution(r"creme de leite|nata", "creme de aveia", "Use oat cream in place of dairy cream."),
    Substitution(r"Käse", "veganer Käse", "Use a plant-based cheese substitute.", "Käse (vegan)"),
    Substitution(r"cheese", "vegan cheese", "Use a plant-based cheese substitute."),
    Substitution(r"queijo", "queijo vegano", "Use a plant-based cheese substitute."),
    Substitution(r"Honig", "Ahornsirup", "Use maple syrup in place of honey."),
    Substitution(r"honey", "maple syrup", "Use maple syrup in place of honey."),
    Substitution(r"mel", "xarope de bordo", "Use maple syrup in place of honey."),
    Substitution(r"Rinderfond|Rinderbrühe|Hühnerfond|Hühnerbrühe", "Gemüsefond",
                 "Use vegetable stock in place of meat stock."),
    Substitution(r"(?:chicken|beef) (?:stock|broth)", "vegetable stock",
                 "Use vegetable stock in place of meat stock."),
    Substitution(r"caldo de (?:carne|frango)", "caldo de legumes",
                 "Use vegetable stock in place of meat stock."),
    Substitution(r"Bratenfond|Bratensaft", "pflanzlicher Bratensatz",
                 "Use the pan juices from the plant-based preparation.", "pflanzlichen Bratensatz"),
    Substitution(r"(?:meat|beef) drippings|pan drippings", "plant-based pan juices",
                 "Use the pan juices from the plant-based preparation."),
    Substitution(r"Rotwein", "veganer Rotwein", "Specify wine labelled vegan.", "Rotwein (vegan)"),
    Substitution(r"red wine", "vegan red wine", "Specify wine labelled vegan."),
    Substitution(r"vinho tinto", "vinho tinto vegano", "Specify wine labelled vegan."),
)

# Match complete words and prioritize longer alternatives, such as chicken stock before chicken.
STEP_TERMS = re.compile(
    r"(?<!\w)(?:"
    + "|".join(
        f"(?P<s{index}>{rule.terms})"
        for index, rule in sorted(
            enumerate(SUBSTITUTIONS), key=lambda item: len(item[1].terms), reverse=True
        )
    )
    + r")(?!\w)",
    re.IGNORECASE,
)

ALREADY_PLANT_PREFIX = re.compile(
    r"\b(?:vegan(?:e[rsnm]?)?|plant[- ]based|dairy[- ]free|non[- ]dairy|peanut|almond|"
    r"cashew|coconut|oat|soy|nut|apple|hazelnut|sunflower|vegetable)\s+$", re.IGNORECASE
)
ALREADY_PLANT_SUFFIX = re.compile(
    r"\s+(?:vegana?s?|veganos?|vegetal|beans?|lettuce|vinegar|eggs?|of tartar|of coconut|"
    r"de (?:soja|coco|aveia|amendoim|amêndoas?|legumes))\b|\s*\(vegan\)", re.IGNORECASE
)
GERMAN_WORDS = re.compile(
    r"\b(?:zum|dann|zugeben|dazugeben|erhitzen|hinzufügen|und|mit|den|soße)\b",
    re.IGNORECASE,
)
GERMAN_INGREDIENTS = re.compile(
    r"\b(?:Bratenstücke|Rinderbraten|Rindfleisch|Räucherspeck|Speck|Zwiebeln?|Mehl|"
    r"Essig|Wasser|Milch|Sahne|Käse|Honig|Rotwein)\b", re.IGNORECASE
)
UNSUPPORTED = re.compile(
    r"\b(?:eggs?|cheese|gelat(?:in|ine|ina)|whey|lard|ghee|pork|fish|salmon|shrimp|"
    r"anchov(?:y|ies)|sausage|stock|broth|worcestershire|milk|butter|bacon|chicken|beef|wine|"
    r"Ei(?:er)?|Käse|Schmalz|Fisch|Lachs|Garnelen|Wurst|Brühe|Milch|Speck|Rindfleisch|Wein|"
    r"ovos?|queijo|soro de leite|banha|peixe|camarão|carne|linguiça|caldo|manteiga|frango|"
    r"molho inglês|vinho)\b", re.IGNORECASE
)


def _join_words(value: str) -> str:
    return re.sub(r"(?<=\w)-[ \t]*\r?\n\s*(?=\w)", "", value)


def _replacement(rule: Substitution, german: bool, in_step: bool = False) -> str:
    if german and rule.replacement == "vegan butter":
        return "vegane Butter"
    return (rule.step_replacement if in_step else None) or rule.replacement


def _plant_term(before: str, after: str) -> bool:
    return bool(
        ALREADY_PLANT_PREFIX.search(before)
        or re.search(r"\bvegan (?:cream|red)\s+$", before, re.I)
        or ALREADY_PLANT_SUFFIX.match(after)
    )


def _has_unsupported(value: str) -> bool:
    return any(
        not _plant_term(value[:match.start()], value[match.end():])
        for match in UNSUPPORTED.finditer(value)
    )


def _substitute_step(value: str, use_roast_rules: bool, german: bool) -> tuple[str, list[str]]:
    text = _join_words(value)
    reasons = []

    def replace(match: re.Match) -> str:
        matched = match.group()
        before, after = text[:match.start()], text[match.end():]
        if _plant_term(before, after):
            return matched
        if matched.casefold() == "cream" and re.match(
            r"\s+(?:together\b|(?:the\s+)?(?:vegan\s+)?butter\b)", after, re.I
        ):
            return matched
        rule = SUBSTITUTIONS[int(match.lastgroup[1:])]
        if matched.casefold() == "milk" and re.match(r"\s+chocolate\b", after, re.I):
            return matched
        # Preserve the verb in "zum Braten". The adjective may precede the noun.
        if matched.casefold() == "braten" and (
            not use_roast_rules or not re.search(
                r"\b(?:der|den|dem|des)\s+(?:\w+(?:en|er|em|e)\s+)?$", before, re.I
            )
        ):
            return matched
        reasons.append(rule.reason)
        replacement = _replacement(rule, german, in_step=True)
        if matched[0].isupper() and (not before or before.rstrip().endswith((".", "!", "?"))):
            return replacement[0].upper() + replacement[1:]
        return replacement

    result = STEP_TERMS.sub(replace, text)
    return (result if reasons else value), reasons


def _adapt_roast_step(step: dict, path: str, change: Callable) -> None:
    instruction = step["instruction"]
    condition = step["condition"]
    duration = step["duration"]
    if instruction["value"] is None:
        return
    text = _join_words(instruction["value"])
    when = _join_words(condition["value"] or "")
    timing = f"{when} {_join_words(duration['value'] or '')} {text}"

    if re.search(r"\bgießt man das Essigwasser heiß darüber\b", text, re.I) and re.search(
        r"\bTag\b", timing, re.I
    ):
        reason = "Cool the marinade before pouring it over seitan and omit the meat-only schedule."
        change(f"{path}/instruction", instruction, "Die Marinade abkühlen lassen und über den Seitan gießen.", reason)
        for key, field in (("condition", condition), ("duration", duration)):
            if field["value"] is not None:
                change(f"{path}/{key}", field, None, reason)
    elif re.search(r"\bgießt man\s+es kalt darauf\b", text, re.I) and re.search(
        r"\bTagen?\b", timing, re.I
    ):
        reason = "Use the marinade with seitan without the original multi-day meat schedule."
        change(f"{path}/instruction", instruction,
               "Den Seitan abgedeckt im Kühlschrank in der Marinade ziehen lassen.", reason)
        for key, field in (("condition", condition), ("duration", duration)):
            if field["value"] is not None:
                change(f"{path}/{key}", field, None, reason)
    elif re.search(r"\bden Braten langsam mit den Zutaten garbrötscheln\b", text, re.I):
        reason = "Heat the seitan in the liquid instead of slow-cooking meat."
        change(f"{path}/instruction", instruction,
               "Den Seitan mit den Zutaten bei kleiner Hitze ziehen lassen, bis er heiß ist.", reason)
    elif re.search(r"\bden Braten\b.*\beine Stunde vor dem Anrichten\b", text, re.I | re.S):
        reason = "Coat the seitan in the sauce without a meat-specific resting time."
        change(f"{path}/instruction", instruction, "Den Seitan vor dem Anrichten in der Soße wenden.", reason)
        if condition["value"] is not None:
            change(f"{path}/condition", condition, None, reason)
        if duration["value"] is not None:
            change(f"{path}/duration", duration, None, reason)
    elif re.match(r"Wenn er von allen Seiten knusprig angebraten ist\b", when, re.I):
        change(f"{path}/condition", condition, re.sub(r"^Wenn er\b", "Wenn der Seitan", when),
               "Identify the seitan explicitly in the browning condition.")


def _adapt_rendering(value: str) -> tuple[str, list[str]]:
    # Smoked tofu does not release fat like bacon. Use the vegan butter already in the step.
    result = re.sub(
        r"den Räuchertofu auslassen,\s*vegane Butter\s+dazugeben",
        "die vegane Butter erhitzen und den Räuchertofu darin anbraten",
        value, flags=re.I,
    )
    result = re.sub(r"\bRäuchertofu auslassen\b", "Räuchertofu anbraten", result, flags=re.I)
    result = re.sub(r"\b[Rr]ender(?=[^.!?\n]{0,60}\bsmoked tofu\b)",
                    lambda match: "Sauté" if match.group()[0].isupper() else "sauté", result)
    result = re.sub(r"\b[Dd]erreta(?=[^.!?\n]{0,60}\btofu defumado\b)",
                    lambda match: "Doure" if match.group()[0].isupper() else "doure", result)
    reasons = ["Sauté smoked tofu instead of rendering bacon fat."] if result != value else []
    browned = re.sub(
        r"\bden abgetrockneten Seitan ins heiße Fett\b",
        "den abgetrockneten Seitan dazu und brät ihn rundum an", result, flags=re.I,
    )
    if browned != result:
        reasons.append("Brown the seitan in the plant-based cooking fat.")
    return browned, reasons


def veganize(
    recipe: dict,
    change: Callable[[str, dict, str | None, str], None],
    warn: Callable[[str, str], None],
) -> None:
    """Apply supported substitutions through the provenance-aware field callbacks."""
    changed_proteins = set()
    roast_ids = set()
    title = recipe["title"]
    german = bool(
        GERMAN_WORDS.search(title["value"] or "")
        or any(GERMAN_INGREDIENTS.search(_join_words(item["name"]["value"] or ""))
               for item in recipe["ingredients"])
        or any(GERMAN_WORDS.search(step["instruction"]["value"] or "") for step in recipe["steps"])
    )
    for index, ingredient in enumerate(recipe["ingredients"]):
        field = ingredient["name"]
        path = f"/ingredients/{index}/name"
        value = _join_words(field["value"] or "")
        rule = next(
            (rule for rule in SUBSTITUTIONS if re.fullmatch(rule.terms, value, re.I)), None
        )
        if rule:
            change(path, field, _replacement(rule, german), rule.reason)
            if rule.check_cooking:
                changed_proteins.add(ingredient["id"])
                if ingredient["quantity"]["value"] and not ingredient["unit"]["value"]:
                    warn(path, "Check how the original count translates to the plant-based ingredient.")
            if rule is SUBSTITUTIONS[0]:
                roast_ids.add(ingredient["id"])
        elif field["status"] == "needs_review":
            warn(path, "Resolve the ingredient name before checking whether it is vegan.")
        elif _has_unsupported(value):
            warn(path, "Check this ingredient or choose a vegan substitute. No substitution rule matched.")

    if roast_ids and re.fullmatch(r"Sauerbraten", title["value"] or "", re.I):
        change("/title", title, "Veganer Sauerbraten", "Identify the dish as a vegan adaptation.")
    elif title["value"]:
        value, reasons = _substitute_step(title["value"], bool(roast_ids), german)
        if reasons:
            change("/title", title, value, " ".join(dict.fromkeys(reasons)))

    for index, step in enumerate(recipe["steps"]):
        path = f"/steps/{index}"
        # The German roast method includes meat-only marinade and resting instructions.
        # Match their wording so unrelated meat recipes retain their original timing for review.
        if roast_ids:
            _adapt_roast_step(step, path, change)
        changed_instruction = False
        for key in ("instruction", "condition"):
            field = step[key]
            if field["value"] is None:
                continue
            value, reasons = _substitute_step(field["value"], bool(roast_ids), german)
            value, cooking_reasons = _adapt_rendering(value)
            reasons.extend(cooking_reasons)
            if reasons and value != field["value"]:
                change(f"{path}/{key}", field, value, " ".join(dict.fromkeys(reasons)))
                changed_instruction |= key == "instruction"
            if _has_unsupported(value):
                warn(f"{path}/{key}", "Check the step for animal-derived ingredients without a matching rule.")

        if step["duration"]["value"] and (
            changed_proteins.intersection(step["ingredient_ids"])
            or (changed_instruction and step["duration"]["status"] == "stated")
        ):
            warn(f"{path}/duration", "Check the original timing for the plant-based substitution.")
