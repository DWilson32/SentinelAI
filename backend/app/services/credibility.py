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

Not recognised is not the same as unreliable; it means unverified. The gate
below only asks such reports to be corroborated before they can be rated high
or critical, which is what an analyst would do.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SourceEvidence:
    title: str
    publisher: str
    credibility: float


SEVERITY_ORDER = ("low", "medium", "high", "critical")


def independent_reports(sources: Iterable[SourceEvidence], minimum: float) -> int:
    """Distinct publishers among reports with distinct headlines, counting only
    sources of at least `minimum` credibility. Syndicated copies of one wire
    story share a headline, so they count once."""
    seen_headlines: set[str] = set()
    publishers: set[str] = set()
    for source in sorted(sources, key=lambda s: -s.credibility):
        if source.credibility < minimum:
            continue
        headline = normalized_title(source.title)
        if headline in seen_headlines:
            continue
        seen_headlines.add(headline)
        publishers.add(_name(source.publisher))
    return len(publishers)


def gate(model_severity: str, sources: list[SourceEvidence]) -> tuple[str, str | None]:
    """The severity an incident may carry given its evidence, with a note when
    that is lower than the risk model's rating.

    Critical needs an official agency or wire service, or two independent
    reports from recognised sources. High needs one recognised source, or two
    independent reports from anything above user-generated content.
    """
    best = max((s.credibility for s in sources), default=0.0)
    allowed = "medium"
    if best >= WIRE or independent_reports(sources, NEWSROOM) >= 2:
        allowed = "critical"
    elif best >= NEWSROOM or independent_reports(sources, UNRECOGNISED) >= 2:
        allowed = "high"

    if SEVERITY_ORDER.index(model_severity) <= SEVERITY_ORDER.index(allowed):
        return model_severity, None
    if allowed == "high":
        reason = "critical needs an official or wire source, or two independent recognised reports"
    elif len(sources) == 1:
        reason = "one report, from a source not on the recognised list"
    else:
        reason = "no recognised source, and its reports are not independent of each other"
    return allowed, f"Capped at {allowed} from {model_severity}: {reason}."


def apply_evidence(incident: IncidentModel) -> None:
    """Set an incident's severity from its risk score, capped by the evidence
    of its current sources, with the status and actions that follow from it.

    Computed from the score rather than the stored severity, so a cap lifts
    again once a corroborating report arrives.
    """
    evidence = [SourceEvidence(s.title, s.publisher, s.credibility_score) for s in incident.sources]
    severity, note = gate(risk_model.severity_for(incident.risk_score), evidence)
    incident.severity = severity
    incident.severity_note = note
    incident.status = "investigating" if severity in {"high", "critical"} else "monitoring"
    incident.recommended_actions = recommended_actions(incident.category, severity)
