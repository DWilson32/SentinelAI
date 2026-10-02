"""How much a report's source can be trusted, and what that allows an incident
to be rated.

Credibility used to be set per feed, so every public-feed report scored 0.82:
USGS, Reuters and an unknown website alike. It now comes from the publisher,
by type of organisation rather than by any judgement of its politics:

* official and scientific agencies (USGS, GDACS, UN agencies)      0.95
* international wire services (Reuters, AP, AFP)                  0.90
* established newsrooms on the list below                          0.80
* anything not recognised                                          0.55
* user-generated platforms (Facebook, X, YouTube, Telegram, ...)   0.30

Not recognised is not the same as unreliable; it means unverified. An
incident's credibility combines its independent sources (assess), and its
severity is capped until that credibility supports it (gate), which is what an
analyst would do: ask for corroboration, and not count reprints as such.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from urllib.parse import urlparse

from app.db.models import IncidentModel
from app.ml.risk_model import risk_model
from app.services.headlines import normalized_title
from app.services.playbook import recommended_actions

OFFICIAL, WIRE, NEWSROOM, UNRECOGNISED, USER_GENERATED = 0.95, 0.90, 0.80, 0.55, 0.30

_TIERS: dict[float, dict[str, tuple[str, ...]]] = {
    OFFICIAL: {
        "names": (
            "usgs earthquake hazards program", "usgs", "gdacs", "reliefweb", "un news", "ocha", "unhcr",
            "unicef", "world health organization", "who", "world food programme", "wfp", "noaa",
            "national weather service", "fema", "india meteorological department", "ndma", "copernicus",
        ),
        "domains": (
            "usgs.gov", "gdacs.org", "reliefweb.int", "un.org", "unocha.org", "unhcr.org", "unicef.org",
            "who.int", "wfp.org", "noaa.gov", "weather.gov", "fema.gov", "imd.gov.in", "ndma.gov.in",
            "copernicus.eu",
        ),
    },
    WIRE: {
        "names": ("reuters", "ap news", "associated press", "the associated press", "afp", "agence france-presse"),
        "domains": ("reuters.com", "apnews.com", "afp.com"),
    },
    NEWSROOM: {
        "names": (
            "bbc", "bbc news", "npr", "the washington post", "washington post", "the new york times",
            "new york times", "wsj", "the wall street journal", "wall street journal", "bloomberg",
            "financial times", "the guardian", "guardian", "cnn", "cbs news", "nbc news", "abc news",
            "al jazeera", "le monde", "dw", "deutsche welle", "france 24", "the economist",
            "the sydney morning herald", "sydney morning herald", "the times of israel", "times of israel",
            "haaretz", "the kyiv independent", "kyiv independent", "ukrinform", "anadolu ajansı",
            "anadolu agency", "yonhap", "kyodo news", "nhk", "the hindu", "the times of india",
            "times of india", "hindustan times", "the economic times", "economic times", "dawn",
            "arab news", "al-monitor", "middle east eye", "the new arab", "lloyd's list",
            "stars and stripes", "the new humanitarian", "el país", "der spiegel", "the conversation",
            "the media line", "international business times", "newsnation", "politico", "axios",
        ),
        "domains": (
            "bbc.com", "bbc.co.uk", "npr.org", "washingtonpost.com", "nytimes.com", "wsj.com",
            "bloomberg.com", "ft.com", "theguardian.com", "cnn.com", "cbsnews.com", "nbcnews.com",
            "abcnews.go.com", "aljazeera.com", "lemonde.fr", "dw.com", "france24.com", "economist.com",
            "smh.com.au", "timesofisrael.com", "haaretz.com", "kyivindependent.com", "ukrinform.net",
            "aa.com.tr", "yna.co.kr", "kyodonews.net", "nhk.or.jp", "thehindu.com",
            "timesofindia.indiatimes.com", "hindustantimes.com", "economictimes.indiatimes.com",
            "dawn.com", "arabnews.com", "al-monitor.com", "middleeasteye.net", "newarab.com",
            "lloydslist.com", "stripes.com", "thenewhumanitarian.org", "elpais.com", "spiegel.de",
            "theconversation.com", "themedialine.org", "ibtimes.com", "newsnationnow.com",
            "politico.com", "axios.com",
        ),
    },
    USER_GENERATED: {
        "names": ("facebook", "x", "twitter", "youtube", "tiktok", "instagram", "reddit", "telegram", "medium", "substack"),
        "domains": (
            "facebook.com", "x.com", "twitter.com", "youtube.com", "tiktok.com", "instagram.com",
            "reddit.com", "t.me", "medium.com", "substack.com", "blogspot.com", "wordpress.com",
        ),
    },
}

# Operator-entered reports come from an analyst, not an unknown website.
_PROVIDER_DEFAULTS = {"manual": 0.70, "mock": 0.74}

# Feeds that link through an aggregator: the host says nothing about the publisher.
_AGGREGATOR_HOSTS = ("news.google.com",)


def _host(value: str) -> str:
    host = (urlparse(value).hostname or value).lower()
    return host[4:] if host.startswith("www.") else host


def _name(publisher: str) -> str:
    # "Le Monde.fr" and "Bloomberg.com" are the same outlets as "Le Monde" and "Bloomberg".
    name = re.sub(r"\s+", " ", publisher.strip(" -").casefold())
    return re.sub(r"\.(com|fr|org|net|co\.uk)$", "", name)


def publisher_credibility(publisher: str, url: str = "", provider: str = "public") -> float:
    name = _name(publisher)
    hosts = [h for h in (_host(publisher), _host(url)) if h and h not in _AGGREGATOR_HOSTS]
    for score, tier in _TIERS.items():
        if name in tier["names"]:
            return score
        for host in hosts:
            if any(host == domain or host.endswith("." + domain) for domain in tier["domains"]):
                return score
    return _PROVIDER_DEFAULTS.get(provider, UNRECOGNISED)


# Automated feeds publish instrument data, not separate reporting: GDACS builds
# its earthquake alerts from the same seismic readings as USGS (an identical
# epicentre in every live pair), so between them they count as one source.
STRUCTURED_HOSTS = ("earthquake.usgs.gov", "gdacs.org")

# Headlines sharing this much of their wording, numbers aside, are one story
# reprinted. On the live data every reprint scored 1.0, and every independent
# report of the same event 0.41 or less. Only headlines can be compared: the
# feeds carry no article text.
SAME_STORY_OVERLAP = 0.8

# Combined credibility a severity needs. One wire or official source reaches
# critical; one recognised newsroom, or two independent unrecognised sites,
# reach high.
CRITICAL_NEEDS, HIGH_NEEDS = 0.90, 0.75

SEVERITY_ORDER = ("low", "medium", "high", "critical")


def is_structured(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == known or host.endswith("." + known) for known in STRUCTURED_HOSTS)


@dataclass(frozen=True)
class SourceEvidence:
    title: str
    publisher: str
    credibility: float
    url: str = ""
    id: str = ""
    published_at: datetime | None = None


@dataclass(frozen=True)
class Evidence:
    # Combined across independent origins; see assess().
    credibility: float
    independent_sources: int
    origins: list[str]
    # Sources that repeat another's report: {"source_id", "publisher", "copy_of", "reason"}.
    copies: list[dict]

    def as_dict(self) -> dict:
        return {
            "credibility": self.credibility,
            "independent_sources": self.independent_sources,
            "origins": self.origins,
            "copies": self.copies,
        }


def _headline_words(title: str) -> set[str]:
    # Numbers masked: "kills 33" and "kills 49" are one story as its toll is updated.
    return set(re.sub(r"\d+", "#", normalized_title(title)).split())


def same_story(a: str, b: str) -> bool:
    words_a, words_b = _headline_words(a), _headline_words(b)
    if not words_a or not words_b:
        return False
    return len(words_a & words_b) / len(words_a | words_b) >= SAME_STORY_OVERLAP


def _earliest_first(source: SourceEvidence) -> tuple[float, float]:
    # The most credible member is the original; among equals, the earliest.
    return source.credibility, -(source.published_at.timestamp() if source.published_at else 0.0)


def assess(sources: list[SourceEvidence]) -> Evidence:
    """Group an incident's sources by origin and combine their credibility.

    A reprint belongs to the outlet that wrote it, and several articles from one
    outlet are one newsroom's reporting, so neither adds independent support.
    Automated feeds count once between them, as do user-generated posts, the
    easiest of all to copy or coordinate. Independent origins then combine as
    independent chances of the report being true, 1 - (1 - c1)(1 - c2)...,
    capped at 0.99: two unrecognised sites agreeing count about as much as one
    recognised newsroom.
    """
    feeds = [s for s in sources if is_structured(s.url)]
    social = [s for s in sources if not is_structured(s.url) and s.credibility <= USER_GENERATED]
    reporting = [s for s in sources if not is_structured(s.url) and s.credibility > USER_GENERATED]

    parent = list(range(len(reporting)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i, j in combinations(range(len(reporting)), 2):
        if same_story(reporting[i].title, reporting[j].title):
            parent[root(j)] = root(i)
    stories: dict[int, list[SourceEvidence]] = {}
    for index, source in enumerate(reporting):
        stories.setdefault(root(index), []).append(source)

    origins: dict[str, float] = {}
    labels: dict[str, str] = {}
    copies: list[dict] = []
    for members in stories.values():
        original = max(members, key=_earliest_first)
        key = _name(original.publisher)
        origins[key] = max(origins.get(key, 0.0), original.credibility)
        labels.setdefault(key, original.publisher)
        for member in members:
            if _name(member.publisher) == key:
                continue  # the same newsroom, not a copy
            same = normalized_title(member.title) == normalized_title(original.title)
            copies.append(
                {
                    "source_id": member.id,
                    "publisher": member.publisher,
                    "copy_of": original.publisher,
                    "reason": "same headline" if same else "near-identical headline",
                }
            )
    if feeds:
        original = max(feeds, key=_earliest_first)
        origins["automated feeds"] = original.credibility
        labels["automated feeds"] = original.publisher
        copies += [
            {"source_id": s.id, "publisher": s.publisher, "copy_of": original.publisher, "reason": "same instrument data"}
            for s in feeds
            if s is not original
        ]
    if social:
        origins["user-generated posts"] = USER_GENERATED
        labels["user-generated posts"] = "user-generated posts"

    doubt = 1.0
    for credibility in origins.values():
        doubt *= 1 - credibility
    combined = round(min(0.99, 1 - doubt), 3) if origins else 0.0
    return Evidence(combined, len(origins), [labels[key] for key in origins], copies)


def gate(model_severity: str, evidence: Evidence) -> tuple[str, str | None]:
    """The severity the evidence supports, with a note when it is lower than
    the risk model's rating. More independent sources raise credibility, never
    the risk score: they make a report likelier to be true, not the event worse."""
    if evidence.credibility >= CRITICAL_NEEDS:
        allowed = "critical"
    elif evidence.credibility >= HIGH_NEEDS:
        allowed = "high"
    else:
        allowed = "medium"
    if SEVERITY_ORDER.index(model_severity) <= SEVERITY_ORDER.index(allowed):
        return model_severity, None
    needs = CRITICAL_NEEDS if model_severity == "critical" else HIGH_NEEDS
    count = evidence.independent_sources
    return allowed, (
        f"Capped at {allowed} from {model_severity}: credibility {evidence.credibility:.0%} from "
        f"{count} independent source{'' if count == 1 else 's'}; {model_severity} needs {needs:.0%}."
    )


def apply_evidence(incident: IncidentModel) -> None:
    """Set an incident's evidence, and its severity from its risk score capped
    by that evidence, with the status and actions that follow from it.

    Computed from the score rather than the stored severity, so a cap lifts
    again once an independent corroborating report arrives.
    """
    evidence = assess(
        [
            SourceEvidence(s.title, s.publisher, s.credibility_score, s.url, s.id, s.published_at)
            for s in incident.sources
        ]
    )
    severity, note = gate(risk_model.severity_for(incident.risk_score), evidence)
    incident.evidence = evidence.as_dict()
    incident.severity = severity
    incident.severity_note = note
    incident.status = "investigating" if severity in {"high", "critical"} else "monitoring"
    incident.recommended_actions = recommended_actions(incident.category, severity)


def verification_summary(evidence: dict | None, source_count: int) -> dict:
    """What the verification agent reports: the independence analysis above,
    instead of an "agreement" label derived from keyword scores."""
    evidence = evidence or {}
    credibility = float(evidence.get("credibility", 0.0))
    independent = int(evidence.get("independent_sources", 0))
    origins = list(evidence.get("origins", []))
    copies = list(evidence.get("copies", []))
    repeats = "; ".join(f"{c['publisher']} repeats {c['copy_of']} ({c['reason']})" for c in copies)
    finding = (
        f"{source_count} source{'' if source_count == 1 else 's'}, {independent} independent"
        + (f" ({', '.join(origins)})" if origins else "")
        + f"; combined credibility {credibility:.0%}."
        + (f" Not independent: {repeats}." if repeats else "")
    )
    return {
        "finding": finding,
        "credibility": credibility,
        "independent_sources": independent,
        "origins": origins,
        "copies": copies,
        "corroboration": "single source" if independent <= 1 else f"{independent} independent sources",
    }
