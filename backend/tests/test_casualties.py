"""The casualty reader that suggests news labels, on live headline phrasings."""

import pytest

from eval.casualties import Casualties, from_headlines, read, severity


@pytest.mark.parametrize(
    "headline, deaths, injured",
    [
        ("Airstrike near a market in Myanmar's Rakhine state kills 33 people - The Washington Post", 33, False),
        ("Death toll from Myanmar airstrike in Rakhine state rises to 50 - Reuters", 50, False),
        ("Myanmar airstrike death toll hits 50 as families gather bodies", 50, False),
        ("UN alarmed by reports that Myanmar air strike killed 50 - Reuters", 50, False),
        ("3 Palestinians killed in Israeli strike, gunfire in Gaza despite ceasefire", 3, False),
        ("Israel kills eight in Gaza as ceasefire violations continue a year on", 8, False),
        ("At least 50 people were killed and 58 injured when a military jet dropped two bombs", 50, True),
        # A killing with no figure counts as one death.
        ("Israeli Airstrike Kills Senior Hamas Commander in Gaza", 1, False),
        ("Israeli attacks kill Palestinian woman, injure 5 others in Gaza despite ceasefire", 1, True),
        ("Iranian Strike Injured 8 US Marines in Strait of Hormuz", 0, True),
        ("Trump rejects Iran's latest ceasefire proposal", 0, False),
        # "Shelling out" money and a museum's "30,000 treasures" are not casualties.
        ("Cities keep shelling out taxpayer money for sports stadiums", 0, False),
        ("How archaeologists saved 30 , 000 ancient treasures from ISIS", 0, False),
    ],
)
def test_reads_reported_casualties(headline, deaths, injured):
    assert read(headline) == (deaths, injured)


def test_an_incident_takes_its_highest_toll_as_it_rises():
    found = from_headlines(
        [
            "Airstrike near a market in Myanmar's Rakhine state kills 33 people",
            "Death toll from Myanmar airstrike in Rakhine state rises to 50",
        ]
    )
    assert (found.deaths, found.evidence) == (50, "Death toll from Myanmar airstrike in Rakhine state rises to 50")


@pytest.mark.parametrize(
    "found, expected",
    [
        (Casualties(0, False, ""), "low"),
        (Casualties(0, True, ""), "medium"),
        (Casualties(50, False, ""), "medium"),
        (Casualties(250, False, ""), "high"),
        (Casualties(1500, False, ""), "critical"),
    ],
)
def test_severity_follows_pager_orders_of_magnitude(found, expected):
    assert severity(found) == expected
