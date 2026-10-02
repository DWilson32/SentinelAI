"""Read reported deaths and injuries from news headlines, to suggest a label
under RUBRIC.md. Suggestions only: every news label is reviewed by a person.
"""

import re
from dataclasses import dataclass

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "dozen": 12, "dozens": 24, "scores": 40, "hundreds": 200,
    "thousands": 2000,
}
_NUMBER = r"(\d[\d,]*|" + "|".join(NUMBER_WORDS) + r")"
_DEATH_AFTER = re.compile(_NUMBER + r"\s+(?:[A-Za-z’'-]+\s+){0,3}?(?:were\s+|are\s+)?(?:killed|dead|die|died)\b", re.IGNORECASE)
_DEATH_BEFORE = re.compile(r"\b(?:kill|kills|killed|killing)\s+(?:at\s+least\s+)?" + _NUMBER + r"\b", re.IGNORECASE)
_TOLL = re.compile(r"death\s+toll\b[^.;]*?\b(?:to|hits|reaches|passes|tops|at)\s+" + _NUMBER, re.IGNORECASE)
_KILLING = re.compile(r"\b(?:kill|kills|killed|killing)\b", re.IGNORECASE)
_INJURY = re.compile(r"\b(?:injur(?:e|es|ed|ing|y|ies)|wound(?:s|ed)?|hurt)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Casualties:
    deaths: int
    injuries: bool
    evidence: str  # the headline the figure came from, or "" when none was reported


def _value(token: str) -> int:
    token = token.lower().replace(",", "")
    return NUMBER_WORDS.get(token) or int(token)


def read(headline: str) -> tuple[int, bool]:
    """(deaths, injuries) reported in one headline."""
    figures = [_value(m.group(1)) for pattern in (_DEATH_AFTER, _DEATH_BEFORE, _TOLL) for m in pattern.finditer(headline)]
    deaths = max(figures, default=0)
    if deaths == 0 and _KILLING.search(headline):
        deaths = 1  # "airstrike kills senior commander": a killing with no number
    return deaths, bool(_INJURY.search(headline))


def from_headlines(headlines: list[str]) -> Casualties:
    """The highest toll across an incident's headlines: the latest as it rises."""
    best = Casualties(0, False, "")
    injuries = False
    for headline in headlines:
        deaths, injured = read(headline)
        injuries = injuries or injured
        if deaths > best.deaths:
            best = Casualties(deaths, False, headline)
    if best.evidence:
        return Casualties(best.deaths, injuries, best.evidence)
    injured_headline = next((h for h in headlines if read(h)[1]), "")
    return Casualties(0, injuries, injured_headline)


def severity(casualties: Casualties) -> str:
    """The rubric's scale, as PAGER's orders of magnitude of deaths."""
    if casualties.deaths >= 1000:
        return "critical"
    if casualties.deaths >= 100:
        return "high"
    if casualties.deaths >= 1 or casualties.injuries:
        return "medium"
    return "low"
