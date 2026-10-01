ACTIONS_BY_CATEGORY = {
    "Flood": ["Validate affected districts.", "Prepare evacuation and shelter updates.", "Monitor waterborne disease risk."],
    "Wildfire": ["Track perimeter growth.", "Prepare evacuation readiness notices.", "Monitor wind and air quality indicators."],
    "Health": ["Increase testing coverage.", "Monitor hospital capacity.", "Publish verified public health guidance."],
    "Cybersecurity": ["Isolate affected systems.", "Check backups and incident logs.", "Notify response teams and leadership."],
    "Earthquake": ["Assess shaking and damage reports.", "Monitor aftershock risk.", "Check transport and utility disruptions."],
    "Conflict": ["Verify independently reported impacts.", "Track displacement and infrastructure risk.", "Monitor escalation and ceasefire developments."],
}
DEFAULT_ACTIONS = ["Verify source credibility.", "Monitor for corroborating reports.", "Prepare an analyst brief."]


def recommended_actions(category: str, severity: str) -> list[str]:
    actions = ACTIONS_BY_CATEGORY.get(category, DEFAULT_ACTIONS)
    if severity in {"high", "critical"}:
        return ["Escalate to analyst review."] + actions
    return list(actions)
