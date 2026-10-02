"""Score the system against the evaluation set (see RUBRIC.md).

    cd backend && python -m eval.run_eval [--save NAME]

Severity: each labelled incident is rated as the live pipeline rates it -- the
risk model on every source, the highest score, then the evidence gate -- and
compared with its label.

Retrieval: each golden question is answered over the frozen live snapshot in
eval/incidents.jsonl, so the scores do not drift as new incidents arrive. Two
ways, both as production runs them: semantic search (the production embedding
model, chunking, 0.60 floor and top 4) and the keyword fallback.

--save writes the report to eval/results/NAME.json and NAME.md.
"""

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app.core.config import settings
from app.ml.risk_model import risk_model
from app.schemas.risk import RiskPredictionRequest
from app.services.credibility import SourceEvidence, assess, gate
from app.services.embedding_service import embedding_service
from app.services.rag_index_service import document_text, rag_index_service
from app.services.rag_service import rag_service

HERE = Path(__file__).resolve().parent
LEVELS = ["low", "medium", "high", "critical"]


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (HERE / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def rate(row: dict) -> tuple[str, float]:
    """Severity and risk score as the live pipeline would give them."""
    best = max(
        (
            risk_model.predict(
                RiskPredictionRequest(title=s["title"], text=s["text"], category=row["category"], source_count=1)
            )
            for s in row["sources"]
        ),
        key=lambda prediction: prediction.risk_score,
    )
    evidence = assess(
        [
            SourceEvidence(
                s["title"],
                s["publisher"],
                s["credibility"],
                s["url"],
                str(index),
                datetime.fromisoformat(s["published_at"]) if s.get("published_at") else None,
            )
            for index, s in enumerate(row["sources"])
        ]
    )
    severity, _ = gate(best.severity, evidence)
    return severity, best.risk_score


def severity_report(rows: list[dict]) -> dict:
    predictions = [(row, rate(row)[0]) for row in rows]
    matrix = {truth: Counter() for truth in LEVELS}
    for row, predicted in predictions:
        matrix[row["label"]][predicted] += 1

    per_class = {}
    for level in LEVELS:
        tp = matrix[level][level]
        predicted_as = sum(matrix[truth][level] for truth in LEVELS)
        actual = sum(matrix[level].values())
        precision = tp / predicted_as if predicted_as else 0.0
        recall = tp / actual if actual else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[level] = {"precision": precision, "recall": recall, "f1": f1, "support": actual}

    def accuracy(subset):
        return sum(1 for row, predicted in subset if predicted == row["label"]) / len(subset) if subset else 0.0

    return {
        "incidents": len(rows),
        "accuracy": accuracy(predictions),
        "macro_f1": sum(c["f1"] for c in per_class.values()) / len(LEVELS),
        "by_kind": {
            kind: {"accuracy": accuracy([p for p in predictions if p[0]["kind"] == kind]), "n": sum(1 for p in predictions if p[0]["kind"] == kind)}
            for kind in ("hazard", "news")
        },
        "per_class": per_class,
        "confusion": {truth: {predicted: matrix[truth][predicted] for predicted in LEVELS} for truth in LEVELS},
        "predicted_distribution": dict(Counter(predicted for _, predicted in predictions)),
    }


def snapshot_incidents(rows: list[dict]) -> list[SimpleNamespace]:
    incidents = []
    for row in rows:
        severity, risk_score = rate(row)
        incidents.append(
            SimpleNamespace(
                id=row["id"],
                title=row["title"],
                category=row["category"],
                location=row.get("location") or "Global",
                summary=row.get("summary") or "",
                severity=severity,
                risk_score=risk_score,
                sources=[
                    SimpleNamespace(title=s["title"], publisher=s["publisher"], raw_text=s["text"], url=s["url"])
                    for s in row["sources"]
                ],
            )
        )
    return incidents


def semantic_search(incidents: list[SimpleNamespace]):
    """The production index, rebuilt in memory over the snapshot."""
    chunk_owner, texts = [], []
    for incident in incidents:
        for source in incident.sources:
            body = document_text(
                title=incident.title,
                category=incident.category,
                location=incident.location,
                summary=incident.summary,
                source_title=source.title,
                publisher=source.publisher,
                content=source.raw_text,
            )
            for chunk in rag_index_service._chunk_text(body):
                chunk_owner.append(incident.id)
                texts.append(chunk)
    vectors = np.array(embedding_service.embed(texts), dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    def search(query: str) -> list[str]:
        query_vector = np.array(embedding_service.embed([query])[0], dtype=np.float32)
        query_vector /= np.linalg.norm(query_vector)
        similarity = vectors @ query_vector
        top = [i for i in np.argsort(-similarity)[: settings.rag_top_k] if similarity[i] >= settings.rag_min_similarity]
        return list(dict.fromkeys(chunk_owner[i] for i in top))

    return search


def retrieval_report(rows: list[dict], queries: list[dict]) -> dict:
    incidents = snapshot_incidents([row for row in rows if row["origin"] == "live"])
    methods = {
        "semantic": semantic_search(incidents),
        "keyword": lambda query: list(dict.fromkeys(chunk.incident_id for chunk in rag_service.rank_by_keywords(incidents, query))),
    }
    report = {}
    for name, search in methods.items():
        results = []
        for item in queries:
            retrieved = search(item["query"])[: settings.rag_top_k]
            relevant = set(item["relevant"])
            found = len(relevant & set(retrieved))
            results.append(
                {
                    "query": item["query"],
                    "kind": item["kind"],
                    "hit": found > 0,
                    # Recall over what four results could hold: a question with
                    # eight relevant incidents is not failed for citing only four.
                    "recall_at_4": found / min(len(relevant), settings.rag_top_k),
                    "retrieved": retrieved,
                }
            )
        report[name] = {
            "hit_rate": sum(r["hit"] for r in results) / len(results),
            "recall_at_4": sum(r["recall_at_4"] for r in results) / len(results),
            "by_kind": {
                kind: sum(r["recall_at_4"] for r in results if r["kind"] == kind) / max(1, sum(1 for r in results if r["kind"] == kind))
                for kind in ("place", "event", "theme")
            },
            "queries": results,
        }
    return report


def markdown(report: dict) -> str:
    sev, ret = report["severity"], report["retrieval"]
    lines = [
        f"# Evaluation: {report['name']}",
        "",
        f"Model `{report['model']}`, {sev['incidents']} labelled incidents, {len(ret['semantic']['queries'])} golden questions.",
        "",
        "## Severity",
        "",
        f"- Accuracy: **{sev['accuracy']:.0%}** (hazards {sev['by_kind']['hazard']['accuracy']:.0%} of {sev['by_kind']['hazard']['n']}, news {sev['by_kind']['news']['accuracy']:.0%} of {sev['by_kind']['news']['n']})",
        f"- Macro F1: **{sev['macro_f1']:.2f}**",
        "",
        "| Severity | Precision | Recall | F1 | Support |",
        "|---|---|---|---|---|",
    ]
    for level in LEVELS:
        c = sev["per_class"][level]
        lines.append(f"| {level} | {c['precision']:.2f} | {c['recall']:.2f} | {c['f1']:.2f} | {c['support']} |")
    lines += ["", "Confusion (rows: label, columns: predicted):", "", "| | " + " | ".join(LEVELS) + " |", "|---|" + "---|" * len(LEVELS)]
    for truth in LEVELS:
        lines.append(f"| {truth} | " + " | ".join(str(sev["confusion"][truth][p]) for p in LEVELS) + " |")
    lines += ["", "## Retrieval (top 4)", "", "| Method | Hit rate | Recall@4 | Place | Event | Theme |", "|---|---|---|---|---|---|"]
    for name, r in ret.items():
        k = r["by_kind"]
        lines.append(f"| {name} | {r['hit_rate']:.0%} | {r['recall_at_4']:.2f} | {k['place']:.2f} | {k['event']:.2f} | {k['theme']:.2f} |")
    lines += ["", "Per question (recall@4, semantic / keyword):", ""]
    for s, k in zip(ret["semantic"]["queries"], ret["keyword"]["queries"]):
        lines.append(f"- {s['query']}: {s['recall_at_4']:.2f} / {k['recall_at_4']:.2f}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", help="write eval/results/NAME.json and NAME.md")
    args = parser.parse_args()

    rows, queries = load("incidents.jsonl"), load("queries.jsonl")
    report = {
        "name": args.save or "unsaved run",
        "model": risk_model.model_name,
        "severity": severity_report(rows),
        "retrieval": retrieval_report(rows, queries),
    }
    text = markdown(report)
    print(text)
    if args.save:
        out = HERE / "results"
        out.mkdir(exist_ok=True)
        (out / f"{args.save}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (out / f"{args.save}.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
