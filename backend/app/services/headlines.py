import re

from app.services.casualties import read
from app.services.lifecycle import as_utc

_TITLE_SEPARATOR = re.compile(r"\s[-–—|]\s")


def normalized_title(title: str) -> str:
    """Headline without a trailing " - Publisher", in lower case, letters and
    digits only — so syndicated copies of one wire story compare equal."""
    parts = _TITLE_SEPARATOR.split(title)
    head = " ".join(parts[:-1]) if len(parts) > 1 else title
    return re.sub(r"[\W_]+", " ", head.casefold()).strip()


def summarize(text: str) -> str:
    """A report's text as an incident summary: whitespace collapsed, at most 280 characters."""
    compact = " ".join(text.split())
    return compact[:280] + ("..." if len(compact) > 280 else "")


def retitle(incident) -> None:
    """Title an incident by its report with the highest death toll.

    An incident took its first report's headline for good, so a toll counted
    up over a day stayed at its first figure: the Myanmar airstrike still read
    "kills 33 people" after every later report said 50. The earliest report
    giving the highest toll now provides the title and summary. Nothing changes
    when no report gives a toll, or the title already gives the highest.
    """
    reports = [(read(source.title)[0], as_utc(source.published_at), source) for source in incident.sources]
    if not reports:
        return
    deaths, _, source = max(reports, key=lambda report: (report[0], -report[1].timestamp()))
    if deaths and read(incident.title)[0] < deaths:
        incident.title = source.title
        incident.summary = summarize(source.raw_text)
