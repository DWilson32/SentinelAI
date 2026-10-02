import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.llm import chat_completion, model_name
from app.schemas.incident import IncidentDetail
from app.services.credibility import verification_summary


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


def _author(llm_text: str | None) -> str:
    """Who wrote a step's finding: the model, or the template it falls back to."""
    return (model_name() or "model") if llm_text else "template"


def _research_node(state: InvestigationState) -> dict[str, list[AgentStep]]:
    incident = state["incident"]
    source_count = len(incident.get("sources") or [])
    llm_text = chat_completion(
        "You are a crisis research analyst. Summarize collected evidence in 2-3 sentences.",
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


def _verification_node(state: InvestigationState) -> dict[str, list[AgentStep]]:
    incident = state["incident"]
    output = verification_summary(incident.get("evidence"), len(incident.get("sources") or []))
    llm_text = chat_completion(
        "You are a source verification analyst. Using the independence analysis given, "
        "say briefly how well corroborated the report is.",
        (
            f"Incident: {incident.get('title')}\n"
            f"Independence analysis: {output['finding']}\n"
            f"Sources:\n{_sources_block(incident)}"
        ),
    )
    if llm_text:
        output["finding"] = llm_text
    output["written_by"] = _author(llm_text)
    return {"steps": [{"agent_name": "Verification Agent", "output": output}]}


def _prediction_node(state: InvestigationState) -> dict[str, list[AgentStep]]:
    incident = state["incident"]
    risk = incident.get("risk_explanation") or {}
    llm_text = chat_completion(
        "You are a crisis risk forecaster. Explain the risk outlook in 2 sentences using only provided data.",
        (
            f"Title: {incident.get('title')}\n"
            f"Severity: {incident.get('severity')}\n"
            f"Risk score: {incident.get('risk_score')}\n"
            f"Drivers: {', '.join(risk.get('drivers') or [])}\n"
            f"Summary: {incident.get('summary')}"
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


def _strategy_node(state: InvestigationState) -> dict[str, list[AgentStep]]:
    incident = state["incident"]
    actions = incident.get("recommended_actions") or []
    prior = [step for step in state.get("steps", []) if step["agent_name"] == "Prediction Agent"]
    prediction = prior[-1]["output"] if prior else {}
    llm_text = chat_completion(
        "You are an emergency strategy planner. Return 3 short recommended actions as a bullet list.",
        (
            f"Incident: {incident.get('title')}\n"
            f"Severity: {incident.get('severity')}\n"
            f"Risk: {prediction.get('risk_score')}\n"
            f"Existing actions: {actions}"
        ),
    )
    if llm_text:
        parsed = [line.lstrip("-• ").strip() for line in llm_text.splitlines() if line.strip()]
        recommended = parsed[:5] if parsed else actions
    else:
        recommended = actions
    output = {
        "finding": llm_text or "Strategy recommendations derived from incident playbook.",
        "recommended_actions": recommended,
        "written_by": _author(llm_text),
    }
    return {"steps": [{"agent_name": "Strategy Agent", "output": output}]}


def _report_node(state: InvestigationState) -> dict[str, list[AgentStep]]:
    incident = state["incident"]
    prior_outputs = {step["agent_name"]: step["output"] for step in state.get("steps", [])}
    strategy = prior_outputs.get("Strategy Agent", {})
    llm_text = chat_completion(
        "You are an intelligence briefer. Write a concise executive brief (max 120 words).",
        (
            f"Incident: {incident.get('title')}\n"
            f"Location: {incident.get('location')}\n"
            f"Summary: {incident.get('summary')}\n"
            f"Research: {prior_outputs.get('Research Agent', {})}\n"
            f"Verification: {prior_outputs.get('Verification Agent', {})}\n"
            f"Prediction: {prior_outputs.get('Prediction Agent', {})}\n"
            f"Strategy: {strategy}"
        ),
    )
    brief = llm_text or (
        f"{incident.get('title')} in {incident.get('location')} remains {incident.get('severity')} "
        f"(risk {incident.get('risk_score')}/100). {incident.get('summary')} "
        f"Priority actions: {'; '.join((strategy.get('recommended_actions') or [])[:3])}."
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
    graph.add_edge("verification", "prediction")
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
