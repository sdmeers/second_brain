"""Best-effort masking of structured personal identifiers.

``mask`` swaps identifiers for placeholders before text goes to a model provider and
``unmask`` restores them locally afterwards. The mapping exists only in memory: never log
it, store it or send it anywhere. ``redact`` is the one-way version for log lines.

Only identifiers with a recognisable shape are caught (UK formats). Names, addresses and
anything free-form are not.
"""

import re


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _nhs_ok(digits: str) -> bool:
    """NHS numbers carry a mod-11 check digit; this keeps phone-like numbers out."""
    check = 11 - sum(int(d) * w for d, w in zip(digits[:9], range(10, 1, -1), strict=True)) % 11
    check = 0 if check == 11 else check
    return check != 10 and check == int(digits[9])


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


# (label, pattern, validator on the matched text, capture group holding the identifier)
_RULES = [
    ("CARD", re.compile(r"(?<!\d)(\d(?:[ -]?\d){12,18})(?!\d)"), lambda m: _luhn_ok(_digits(m)), 1),
    ("LICENCE", re.compile(r"\b([A-Z9]{5}\d{6}[A-Z9]{2}\d[A-Z]{2})\b"), None, 1),
    ("NINO", re.compile(r"\b([A-CEGHJ-PR-TW-Z]{2} ?\d{2} ?\d{2} ?\d{2} ?[A-D])\b", re.I), None, 1),
    ("NHS", re.compile(r"(?<!\d)(\d{3}[ -]?\d{3}[ -]?\d{4})(?!\d)"), lambda m: _nhs_ok(_digits(m)), 1),
    # These shapes collide with dates and ordinary numbers, so they need a nearby keyword.
    ("SORTCODE", re.compile(r"sort\s*code\D{0,12}(\d{2}[ -]?\d{2}[ -]?\d{2})(?!\d)", re.I), None, 1),
    ("ACCOUNT", re.compile(r"acc(?:oun)?t\.?\s*(?:no\.?|number|num)?\D{0,12}(\d{8})(?!\d)", re.I), None, 1),
    ("PASSPORT", re.compile(r"passport\D{0,20}(\d{9})(?!\d)", re.I), None, 1),
]


def mask(text: str) -> tuple[str, dict[str, str]]:
    """Return the text with identifiers replaced by ``<LABEL_n>`` and the mapping back."""
    mapping: dict[str, str] = {}
    by_value: dict[str, str] = {}

    for label, pattern, validator, group in _RULES:
        def swap(m, label=label, validator=validator, group=group):
            value = m.group(group)
            if validator and not validator(value):
                return m.group(0)
            token = by_value.get(value)
            if token is None:
                token = f"<{label}_{len(mapping) + 1}>"
                mapping[token] = value
                by_value[value] = token
            start, end = m.start(group) - m.start(0), m.end(group) - m.start(0)
            whole = m.group(0)
            return whole[:start] + token + whole[end:]
        text = pattern.sub(swap, text)
    return text, mapping


def unmask(text: str, mapping: dict[str, str]) -> str:
    for token, value in mapping.items():
        text = text.replace(token, value)
    return text


def redact(text: str) -> str:
    """One-way masking for log lines: identifiers become ``<LABEL>`` with no way back."""
    masked, mapping = mask(text)
    for token in mapping:
        masked = masked.replace(token, f"<{token[1:].rsplit('_', 1)[0]}>")
    return masked
