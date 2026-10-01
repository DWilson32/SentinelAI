import re

_TITLE_SEPARATOR = re.compile(r"\s[-–—|]\s")


def normalized_title(title: str) -> str:
    """Headline without a trailing " - Publisher", in lower case, letters and
    digits only — so syndicated copies of one wire story compare equal."""
    parts = _TITLE_SEPARATOR.split(title)
    head = " ".join(parts[:-1]) if len(parts) > 1 else title
    return re.sub(r"[\W_]+", " ", head.casefold()).strip()
