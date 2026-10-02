import json
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.llm import chat_completion, model_name
from app.schemas.incident import IncidentDetail
from app.services.casualties import toll_history
from app.services.credibility import verification_summary
from app.services.playbook import recommended_actions as playbook_actions


class AgentStep(TypedDict):
    agent_name: str
    output: dict[str, Any]


class InvestigationState(TypedDict):
    incident: dict[str, Any]
    rag_context: str
    steps: Annotated[list[AgentStep], operator.add]


def _sources_block(incident: dict[str, Any]) -> str:
    sources = incident.get("sources") or []
    if not sources:
        return "No source documents on file."
    lines = []
    for source in sources[:6]:
        lines.append(
            f"- {source.get('title')} ({source.get('publisher')}, "
            f"credibility {source.get('credibility_score')}): {str(source.get('raw_text', ''))[:280]}"
        )
    return "\n".join(lines)


# The feeds carry headlines, not articles. Left to itself the model filled the
# gaps from memory -- a live brief said a ceasefire plan would "freeze front-line
# positions", which no source said -- so every step is held to its inputs.
GROUNDED = (
    " Use only the information given below. Do not add facts, figures, names or "
    "claims that are not in it; where it is thin, say what is missing instead."
)


def _author(llm_text: str | None) -> str:
    """Who wrote a step's finding: the model, or the template it falls back to."""
    return (model_name() or "model") if llm_text else "template"


def _json_answer(llm_text: str | None) -> dict[str, Any]:
    """The JSON object in a model's answer, or {} when there is none."""
    try:
        answer = json.loads(llm_text[llm_text.index("{") : llm_text.rindex("}") + 1])
    except (AttributeError, TypeError, ValueError):
        return {}
    return answer if isinstance(answer, dict) else {}


def _toll_lines(toll: dict[str, Any]) -> str:
    return "\n".join(
        f"- {report['deaths']} dead: {report['title']} ({report['publisher']}, "
        f"{report['published_at'][:16].replace('T', ' ')} UTC)"
        for report in toll["reports"]
    )


def _flagged_toll(state: InvestigationState) -> dict[str, Any] | None:
    """The disagreement on the toll that verification found, if any."""
    checks = [step["output"] for step in state.get("steps", []) if step["agent_name"] == "Verification Agent"]
    return checks[-1].get("toll") if checks else None


def _second_pass(state: InvestigationState, agent_name: str) -> dict[str, Any] | None:
    return next(
        (
            step["output"]
            for step in state.get("steps", [])
            if step["agent_name"] == agent_name and step["output"].get("pass") == 2
        ),
        None,
    )


def _research_node(state: InvestigationState) -> dict[str, list[AgentStep]]:
    incident = state["incident"]
    toll = _flagged_toll(state)
    if toll:
        return _toll_research(incident, toll)
    source_count = len(incident.get("sources") or [])
    llm_text = chat_completion(
        # Attribution keeps it to what each report says: unattributed, "a Sudan official"
        # became "a current government spokesperson".
        "You are a crisis research analyst. Summarize collected evidence in 2-3 sentences, attributing "
        "each claim to the outlet that reports it, in the reports' own words for who said what."
        + GROUNDED,
        (
            f"Incident: {incident.get('title')}\n"
            f"Category: {incident.get('category')}\n"
            f"Location: {incident.get('location')}\n"
            f"Summary: {incident.get('summary')}\n\n"
            f"Sources:\n{_sources_block(incident)}\n\n"
            f"Vector retrieval context:\n{state.get('rag_context', '')}"
        ),
    )
    output = {
        "finding": llm_text
        or (
            f"Collected {source_count} source document(s) for {incident.get('category')} incident "
            f"in {incident.get('location')}."
        ),
        "source_count": source_count,
        "publishers": list({s.get("publisher") for s in incident.get("sources") or [] if s.get("publisher")}),
        "written_by": _author(llm_text),
    }
    return {"steps": [{"agent_name": "Research Agent", "output": output}]}


def _toll_research(incident: dict[str, Any], toll: dict[str, Any]) -> dict[str, list[AgentStep]]:
    """The second pass verification asks for: do the differing tolls describe one event?"""
    question = "The reports give different death tolls: one event whose toll changed, or different events?"
    llm_text = chat_completion(
        # Asked outright for "the latest figure", it called a second attack's single
        # death the latest toll of the first; the figure only follows if it is one event.
        "You are a crisis research analyst. The reports below give different death tolls for what was "
        "filed as one incident. Comparing who was killed, where and when, say in 2-3 sentences whether "
        "they describe one event whose toll changed or different events. Only if it is one event, say "
        "which figure is the latest." + GROUNDED,
        (
            f"Incident: {incident.get('title')}\n"
            f"Location: {incident.get('location')}\n\n"
            f"Reports, oldest first:\n{_toll_lines(toll)}"
        ),
    )
    first, last = toll["reports"][0], toll["reports"][-1]
    if toll["status"] == "rising":
        template = (
            f"The reported death toll rose from {first['deaths']} ({first['publisher']}) to "
            f"{last['deaths']} ({last['publisher']}); the latest figure is {last['deaths']}."
        )
    else:
        template = (
            "A later report gives fewer deaths than an earlier one: the figure was corrected, or the "
            "reports describe different events."
        )
    output = {"finding": llm_text or template, "question": question, "pass": 2, "written_by": _author(llm_text)}
    return {"steps": [{"agent_name": "Research Agent", "output": output}]}


def _verification_node(state: InvestigationState) -> dict[str, list[AgentStep]]:
    incident = state["incident"]
    toll = _flagged_toll(state)
    research = _second_pass(state, "Research Agent")
    if toll and research:
        return _toll_verification(toll, research)
    output = verification_summary(incident.get("evidence"), len(incident.get("sources") or []))
    llm_text = chat_completion(
        "You are a source verification analyst. Using the independence analysis given, "
        "say briefly how well corroborated the report is." + GROUNDED,
        (
            f"Incident: {incident.get('title')}\n"
            f"Independence analysis: {output['finding']}\n"
            f"Sources:\n{_sources_block(incident)}"
        ),
    )
    if llm_text:
        output["finding"] = llm_text
    # Reports that disagree on the toll send the investigation back to research.
    toll = toll_history(incident.get("sources") or [])
    if toll:
        output["toll"] = toll
    output["written_by"] = _author(llm_text)
    return {"steps": [{"agent_name": "Verification Agent", "output": output}]}


def _toll_verification(toll: dict[str, Any], research: dict[str, Any]) -> dict[str, list[AgentStep]]:
    """Check research's account of the differing tolls against the reports themselves."""
    figures = {report["deaths"] for report in toll["reports"]}
    llm_text = chat_completion(
        "You are a source verification analyst. The reports below give different death tolls, and a "
        "researcher has examined them. Check the researcher's conclusion against the reports. Answer in "
        'JSON: {"same_event": "yes", "no" or "unclear", "current_toll": one of the reported figures or '
        'null, "finding": "<1-2 sentences>"}.' + GROUNDED,
        f"Reports, oldest first:\n{_toll_lines(toll)}\n\nResearcher: {research['finding']}",
        json_object=True,
    )
    answer = _json_answer(llm_text)
    finding = str(answer.get("finding") or "").strip()
    try:
        current_toll = int(answer.get("current_toll"))
    except (TypeError, ValueError):
        current_toll = None
    if not finding:
        llm_text = None
        last = toll["reports"][-1]["deaths"]
        if toll["status"] == "rising":
            finding, current_toll = f"The toll rose with each report; the latest figure, {last}, is current.", last
        else:
            finding = "The reports disagree and a later one gives fewer deaths; an analyst should check."
    output = {
        "finding": finding,
        "same_event": answer.get("same_event") if answer.get("same_event") in {"yes", "no", "unclear"} else None,
        # Only a figure some report gave: the model may not settle on a number of its own.
        "current_toll": current_toll if current_toll in figures else None,
        "toll": toll,
        "pass": 2,
        "written_by": _author(llm_text),
    }
    return {"steps": [{"agent_name": "Verification Agent", "output": output}]}


def _after_verification(state: InvestigationState) -> str:
    """The graph's one decision: reports that disagree on the toll go back to
    research for a second pass, once, before anything is predicted from them."""
    check = [step["output"] for step in state["steps"] if step["agent_name"] == "Verification Agent"][-1]
    return "research" if check.get("toll") and check.get("pass") != 2 else "prediction"


def _toll_note(state: InvestigationState) -> str:
    check = _second_pass(state, "Verification Agent")
    return f"\nToll check: {check['finding']}" if check else ""


def _prediction_node(state: InvestigationState) -> dict[str, list[AgentStep]]:
    incident = state["incident"]
    risk = incident.get("risk_explanation") or {}
    llm_text = chat_completion(
        "You are a crisis risk forecaster. Explain the risk outlook in 2 sentences." + GROUNDED,
        (
            f"Title: {incident.get('title')}\n"
            f"Severity: {incident.get('severity')}\n"
            f"Risk score: {incident.get('risk_score')}\n"
            f"Drivers: {', '.join(risk.get('drivers') or [])}\n"
            f"Summary: {incident.get('summary')}"
            f"{_toll_note(state)}"
        ),
    )
    output = {
        "finding": llm_text
        or (
            f"Current assessment: {incident.get('severity')} severity with risk score "
            f"{incident.get('risk_score')}/100."
        ),
        "risk_score": incident.get("risk_score"),
        "severity": incident.get("severity"),
        "confidence": risk.get("confidence", 0.0),
        "drivers": risk.get("drivers") or [],
        "written_by": _author(llm_text),
    }
    return {"steps": [{"agent_name": "Prediction Agent", "output": output}]}


def _chosen(llm_text: str | None, playbook: list[str]) -> dict[str, str]:
    """The playbook actions the model chose, in its order, each with its reason.

    Read from JSON: as prose the model numbered its own ranking ("1. **3 - Monitor
    ...**"), which reads the same as a choice of action 1. Numbers that are not in
    the playbook are dropped, so nothing outside it can be recommended.
    """
    try:
        choices = list(_json_answer(llm_text)["actions"])
    except (KeyError, TypeError):
        return {}
    reasons: dict[str, str] = {}
    for choice in choices:
        try:
            number = int(choice["number"])
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= number <= len(playbook):
            reasons.setdefault(playbook[number - 1], str(choice.get("why") or "").strip())
    return reasons


def _strategy_node(state: InvestigationState) -> dict[str, list[AgentStep]]:
    incident = state["incident"]
    # The actions come from the playbook; the model only orders them for this incident
    # and says why. Asked to write its own, it proposed a "logistics hub near the
    # Sudan-Libya border" from a headline warning of a "Libya-style partition".
    playbook = playbook_actions(str(incident.get("category")), str(incident.get("severity")))
    prior = [step for step in state.get("steps", []) if step["agent_name"] == "Prediction Agent"]
    prediction = prior[-1]["output"] if prior else {}
    numbered = "\n".join(f"{number}. {action}" for number, action in enumerate(playbook, start=1))
    llm_text = chat_completion(
        "You are an emergency strategy planner. From the numbered playbook below, choose the actions "
        "that apply to this incident, most urgent first, and say in one sentence why each applies. "
        'Answer in JSON: {"actions": [{"number": <playbook number>, "why": "<one sentence>"}]}. '
        "Do not suggest actions that are not in the playbook." + GROUNDED,
        (
            f"Incident: {incident.get('title')}\n"
            f"Severity: {incident.get('severity')}\n"
            f"Risk: {prediction.get('risk_score')}{_toll_note(state)}\n"
            f"Sources:\n{_sources_block(incident)}\n\n"
            f"Playbook:\n{numbered}"
        ),
        json_object=True,
    )
    reasons = _chosen(llm_text, playbook)
    if reasons:
        finding = "\n".join(f"- {action} {reason}" for action, reason in reasons.items())
    else:
        # No usable choice: the playbook in its own order, as without a model.
        llm_text = None
        reasons = dict.fromkeys(playbook, "")
        finding = "Strategy recommendations derived from incident playbook."
    output = {
        "finding": finding,
        "recommended_actions": list(reasons),
        "written_by": _author(llm_text),
    }
    return {"steps": [{"agent_name": "Strategy Agent", "output": output}]}


def _report_node(state: InvestigationState) -> dict[str, list[AgentStep]]:
    incident = state["incident"]
    # First passes by name; a second pass on the toll comes in as the toll check.
    prior_outputs = {
        step["agent_name"]: step["output"] for step in state.get("steps", []) if step["output"].get("pass") != 2
    }
    strategy = prior_outputs.get("Strategy Agent", {})
    llm_text = chat_completion(
        "You are an intelligence briefer. Write a concise executive brief (max 120 words) "
        "in which every statement comes from the inputs." + GROUNDED,
        (
            f"Incident: {incident.get('title')}\n"
            f"Location: {incident.get('location')}\n"
            f"Summary: {incident.get('summary')}\n"
            f"Research: {prior_outputs.get('Research Agent', {})}\n"
            f"Verification: {prior_outputs.get('Verification Agent', {})}\n"
            f"Prediction: {prior_outputs.get('Prediction Agent', {})}\n"
            f"Strategy: {strategy}"
            f"{_toll_note(state)}"
        ),
    )
    check = _second_pass(state, "Verification Agent")
    brief = llm_text or (
        f"{incident.get('title')} in {incident.get('location')} remains {incident.get('severity')} "
        f"(risk {incident.get('risk_score')}/100). {incident.get('summary')} "
        + (f"{check['finding']} " if check else "")
        + f"Priority actions: {'; '.join((strategy.get('recommended_actions') or [])[:3])}."
    )
    output = {
        "brief": brief,
        "status": "Executive brief ready.",
        "report_type": "executive_brief",
        "written_by": _author(llm_text),
    }
    return {"steps": [{"agent_name": "Report Agent", "output": output}]}


def build_investigation_graph():
    graph = StateGraph(InvestigationState)
    graph.add_node("research", _research_node)
    graph.add_node("verification", _verification_node)
    graph.add_node("prediction", _prediction_node)
    graph.add_node("strategy", _strategy_node)
    graph.add_node("report", _report_node)

    graph.add_edge(START, "research")
    graph.add_edge("research", "verification")
    graph.add_conditional_edges("verification", _after_verification, {"research": "research", "prediction": "prediction"})
    graph.add_edge("prediction", "strategy")
    graph.add_edge("strategy", "report")
    graph.add_edge("report", END)
    return graph.compile()


_investigation_graph = None


def run_investigation(incident: IncidentDetail, rag_context: str) -> list[AgentStep]:
    global _investigation_graph
    if _investigation_graph is None:
        _investigation_graph = build_investigation_graph()
    result = _investigation_graph.invoke(
        {
            "incident": incident.model_dump(mode="json"),
            "rag_context": rag_context,
            "steps": [],
        }
    )
    return result["steps"]
