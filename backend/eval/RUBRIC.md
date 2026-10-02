# Severity labelling rubric

Every incident in the evaluation set is labelled on one scale, and the scale is borrowed rather than invented. It follows USGS PAGER, which estimates an earthquake's impact as orders of magnitude of fatalities or economic losses, whichever is worse:

| Severity | PAGER alert | Estimated deaths | Typical case |
|----------|-------------|------------------|--------------|
| low      | green       | 0                | No casualties or damage reported |
| medium   | yellow      | 1–99             | Deaths, injuries or damage reported |
| high     | orange      | 100–999          | A deadly disaster or attack |
| critical | red         | 1,000+           | A catastrophe |

## Natural hazards: the agencies' own alerts

- **USGS earthquakes** take their PAGER alert level, read from the report.
  - "Not assigned" counts as **low**. PAGER runs for every event large enough to matter, so no alert means no expected impact.
- **GDACS events** take GDACS's alert colour from the title.
  - **Green** counts as low, **orange** as high and **red** as critical.
  - GDACS has no yellow level, so no flood or cyclone in the set is labelled medium.

These labels are not anyone's opinion, but they are visible in the report text the system reads. A system that uses the alert is using a real input that arrives at ingest, not cheating. Results are therefore reported separately for hazards and for news.

## News reports: reported casualties

News has no official alert, so the label follows the same scale applied to what the reports say:

- The toll is **the reported event's own**, taken from the incident's headlines.
  - When an incident's sources give different figures (33, then 49, then 50, as a toll rises), the latest and highest is used.
  - A war's cumulative death toll is not the toll of the strike being reported.
- **Injuries, or damage, with no deaths** count as **medium**.
- A report of a killing with no number counts as at least one death, so **medium**. Example: "airstrike kills senior commander".
- **No casualties reported** counts as **low**. That covers diplomacy, statements, analysis, markets, events, and anything filed as a crisis that is not one.

Each label records its source. Hazards record the agency and alert level. News records the casualty figure and the headline it came from, or "no casualties reported".

## Golden chat questions

Each question lists the incidents a correct answer should cite. An incident is relevant when it is about the place and kind of event asked about; the date is not considered. The judgements were drafted from the snapshot, then reviewed.
