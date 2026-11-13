"""Parse and scale recipe measures without rounding or guessing units."""

import re
from fractions import Fraction


VULGAR_FRACTIONS = {
    "½": Fraction(1, 2),
    "⅓": Fraction(1, 3),
    "⅔": Fraction(2, 3),
    "¼": Fraction(1, 4),
    "¾": Fraction(3, 4),
    "⅕": Fraction(1, 5),
    "⅖": Fraction(2, 5),
    "⅗": Fraction(3, 5),
    "⅘": Fraction(4, 5),
    "⅙": Fraction(1, 6),
    "⅚": Fraction(5, 6),
    "⅐": Fraction(1, 7),
    "⅛": Fraction(1, 8),
    "⅜": Fraction(3, 8),
    "⅝": Fraction(5, 8),
    "⅞": Fraction(7, 8),
    "⅑": Fraction(1, 9),
    "⅒": Fraction(1, 10),
}
VULGAR_BY_VALUE = {value: char for char, value in VULGAR_FRACTIONS.items()}
VULGAR_PATTERN = "[" + "".join(VULGAR_FRACTIONS) + "]"
SLASH_PATTERN = r"[/⁄∕]"
NUMBER_PATTERN = (
    rf"(?:(?:[0-9]+\s*)?{VULGAR_PATTERN}"
    rf"|(?:[0-9]+\s+)?[0-9]+\s*{SLASH_PATTERN}\s*[0-9]+"
    r"|[0-9]*[.,][0-9]+|[0-9]+)"
)
_RANGE_SEPARATOR = r"\s*[-–—−]\s*|\s+(?:to|bis|a|até|à)\s+"
QUANTITY_PATTERN = rf"{NUMBER_PATTERN}(?:(?:{_RANGE_SEPARATOR}){NUMBER_PATTERN})?"
_NUMBER = re.compile(NUMBER_PATTERN)
_RANGE = re.compile(
    rf"({NUMBER_PATTERN})({_RANGE_SEPARATOR})({NUMBER_PATTERN})",
    re.IGNORECASE,
)

_SMALL_UNITS = {
    "g": "g", "gram": "g", "grams": "g", "gramm": "g", "gramme": "g",
    "grammes": "g", "grama": "g", "gramas": "g",
    "ml": "ml", "milliliter": "ml", "milliliters": "ml", "millilitre": "ml",
    "millilitres": "ml", "mililitro": "ml", "mililitros": "ml",
}
_LARGE_UNITS = {
    "kg": "kg", "kilogram": "kg", "kilograms": "kg", "kilogramm": "kg",
    "kilogramme": "kg", "kilogrammes": "kg", "quilograma": "kg", "quilogramas": "kg",
    "l": "l", "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "litro": "l", "litros": "l",
}
_LARGER = {"g": "kg", "ml": "l"}
_SMALLER = {"kg": "g", "l": "ml"}
_METRIC_BASE = {
    **{alias: (symbol, 1) for alias, symbol in _SMALL_UNITS.items()},
    **{alias: (_SMALLER[symbol], 1000) for alias, symbol in _LARGE_UNITS.items()},
}


def parse_number(text: str) -> Fraction | None:
    """Return an exact nonnegative number, or None if the text is unsupported."""
    text = text.strip()
    if _NUMBER.fullmatch(text) is None:
        return None
    try:
        if text[-1] in VULGAR_FRACTIONS:
            return int(text[:-1].strip() or "0") + VULGAR_FRACTIONS[text[-1]]
        if re.search(SLASH_PATTERN, text):
            numerator, denominator = re.split(SLASH_PATTERN, text)
            parts = numerator.split()
            whole = int(parts[0]) if len(parts) == 2 else 0
            return whole + Fraction(int(parts[-1]), int(denominator))
        # Without a declared locale, 1.000 and 1,000 could each mean one or one thousand.
        if re.fullmatch(r"[1-9][0-9]{0,2}[.,][0-9]{3}", text):
            return None
        return Fraction(text.replace(",", "."))
    except (ValueError, ZeroDivisionError):
        return None


def _decimal_places(denominator: int) -> int | None:
    twos = fives = 0
    while denominator % 2 == 0:
        twos += 1
        denominator //= 2
    while denominator % 5 == 0:
        fives += 1
        denominator //= 5
    return max(twos, fives) if denominator == 1 else None


def format_number(value: Fraction, template: str = "") -> str:
    """Format a number exactly and preserve decimal or fraction notation when practical."""
    if value < 0:
        return "-" + format_number(-value, template)
    if value.denominator == 1:
        return str(value.numerator)
    whole, remainder = divmod(value.numerator, value.denominator)
    fraction = Fraction(remainder, value.denominator)
    if re.search(VULGAR_PATTERN, template) and fraction in VULGAR_BY_VALUE:
        prefix = str(whole) if whole else ""
        return prefix + VULGAR_BY_VALUE[fraction]

    slash = re.search(SLASH_PATTERN, template)
    places = _decimal_places(value.denominator)
    if slash is None and places is not None:
        digits = str(value.numerator * 10**places // value.denominator).zfill(places + 1)
        separator = "," if "," in template else "."
        return digits[:-places] + separator + digits[-places:]

    prefix = f"{whole} " if whole else ""
    separator = slash.group() if slash else "/"
    return f"{prefix}{fraction.numerator}{separator}{fraction.denominator}"


def _metric_unit(values: list[Fraction], unit: str | None) -> tuple[list[Fraction], str | None]:
    if unit is None:
        return values, unit
    normalized = unit.strip().casefold().rstrip(".")
    if normalized in _SMALL_UNITS and min(values) >= 1000:
        converted = [value / 1000 for value in values]
        target = _LARGER[_SMALL_UNITS[normalized]]
    elif normalized in _LARGE_UNITS and 0 < max(values) < 1:
        converted = [value * 1000 for value in values]
        target = _SMALLER[_LARGE_UNITS[normalized]]
    else:
        return values, unit
    # Keep the original unit when conversion would produce a less useful fraction.
    if all((value * 1000).denominator == 1 for value in converted):
        return converted, target
    return values, unit


def _quantity_parts(quantity: str) -> tuple[list[Fraction], list[str], str] | None:
    interval = _RANGE.fullmatch(quantity.strip())
    if interval:
        first, separator, last = interval.groups()
        lower, upper = parse_number(first), parse_number(last)
        if lower is None or upper is None or lower > upper:
            return None
        return [lower, upper], [first, last], separator
    value = parse_number(quantity)
    if value is None:
        return None
    return [value], [quantity], ""


def quantity_matches(
    quantity: str, unit: str | None, other_quantity: str, other_unit: str | None
) -> bool:
    """Compare supported numeric measures using exact conversion for metric units."""
    original = _quantity_parts(quantity)
    other = _quantity_parts(other_quantity)
    if original is None or other is None:
        return False
    normalized = unit.strip().casefold().rstrip(".") if unit else None
    other_normalized = other_unit.strip().casefold().rstrip(".") if other_unit else None
    original_unit, original_factor = _METRIC_BASE.get(normalized, (normalized, 1))
    target_unit, target_factor = _METRIC_BASE.get(other_normalized, (other_normalized, 1))
    return original_unit == target_unit and (
        [value * original_factor for value in original[0]]
        == [value * target_factor for value in other[0]]
    )


def unit_aliases(unit: str) -> tuple[str, ...]:
    """Return compatible unit spellings to look for in recipe prose."""
    normalized = unit.strip().casefold().rstrip(".")
    if normalized not in _METRIC_BASE:
        return (unit,)
    base, _ = _METRIC_BASE[normalized]
    return (unit, *(
        alias for alias, (symbol, _) in _METRIC_BASE.items()
        if symbol == base and alias != unit
    ))


def scale_quantity(
    quantity: str, unit: str | None, factor: Fraction
) -> tuple[str, str | None] | None:
    """Scale a numeric measure or range. Return None for an unspecified measure."""
    if factor <= 0:
        raise ValueError("The serving scale factor must be positive.")
    parts = _quantity_parts(quantity)
    if parts is None:
        return None
    values, templates, separator = parts
    if factor == 1:
        return quantity, unit
    scaled, target_unit = _metric_unit([value * factor for value in values], unit)
    quantity_text = separator.join(
        format_number(value, template) for value, template in zip(scaled, templates)
    )
    return quantity_text, target_unit
