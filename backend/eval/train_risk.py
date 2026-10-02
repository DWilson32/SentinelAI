"""Train the risk model on the evaluation set, and say how well it does on
incidents it has not seen.

    cd backend && pip install -r requirements-dev.txt && python -m eval.train_risk

A multinomial logistic regression over app/ml/features.py. Every source is an
example labelled with its incident's severity; an incident is rated by its
most severe source, then capped by the evidence gate, as in production.

The headline numbers are 5-fold cross-validated, with all of an incident's
sources kept in the same fold, so each incident is rated by a model that never
saw it. The model is then fitted on everything and its weights written to
app/ml/risk_model_v3.json; production needs only that file, not scikit-learn.
"""

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold

from app.ml.features import FEATURES, vector
from app.services.credibility import SourceEvidence, assess, gate
from eval.run_eval import LEVELS, load, severity_metrics

OUTPUT = Path(__file__).resolve().parents[1] / "app" / "ml" / "risk_model_v3.json"
C = 1.0  # fixed in advance rather than tuned on the folds being reported


def examples(rows: list[dict]):
    X, y, groups = [], [], []
    for row in rows:
        for source in row["sources"]:
            X.append(vector(source["title"], source["text"], row["category"]))
            y.append(LEVELS.index(row["label"]))
            groups.append(row["id"])
    return np.array(X), np.array(y), np.array(groups)


def incident_severity(model, row: dict) -> str:
    """The most severe source, capped by the evidence gate."""
    probabilities = model.predict_proba(
        np.array([vector(s["title"], s["text"], row["category"]) for s in row["sources"]])
    )
    level = LEVELS[int(probabilities.argmax(axis=1).max())]
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
    return gate(level, evidence)[0]


def new_model() -> LogisticRegression:
    return LogisticRegression(C=C, class_weight="balanced", max_iter=5000)


def main() -> None:
    rows = load("incidents.jsonl")
    X, y, groups = examples(rows)

    # Cross-validated: each incident predicted by a model trained without it.
    predicted: dict[str, str] = {}
    folds = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=0)
    for train, test in folds.split(X, y, groups):
        model = new_model().fit(X[train], y[train])
        for incident_id in set(groups[test]):
            row = next(r for r in rows if r["id"] == incident_id)
            predicted[incident_id] = incident_severity(model, row)
    cv = severity_metrics(rows, [predicted[row["id"]] for row in rows])

    final = new_model().fit(X, y)
    OUTPUT.write_text(
        json.dumps(
            {
                "model": "sentinel-logistic-risk-v3",
                "trained": date.today().isoformat(),
                "features": list(FEATURES),
                "classes": LEVELS,
                "coef": final.coef_.round(5).tolist(),
                "intercept": final.intercept_.round(5).tolist(),
                "training_set": {"incidents": len(rows), "sources": len(y), "C": C, "class_weight": "balanced"},
                "cross_validated": {
                    "accuracy": round(cv["accuracy"], 4),
                    "macro_f1": round(cv["macro_f1"], 4),
                    "by_kind": cv["by_kind"],
                    "per_class": cv["per_class"],
                    "confusion": cv["confusion"],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"{len(rows)} incidents, {len(y)} source examples, {len(FEATURES)} features\n")
    print(f"Cross-validated accuracy {cv['accuracy']:.0%}, macro F1 {cv['macro_f1']:.2f}")
    for kind, values in cv["by_kind"].items():
        print(f"  {kind}: {values['accuracy']:.0%} of {values['n']}")
    print("\n            precision  recall  f1    support")
    for level in LEVELS:
        c = cv["per_class"][level]
        print(f"  {level:9s} {c['precision']:9.2f} {c['recall']:7.2f} {c['f1']:5.2f} {c['support']:8d}")
    print("\nConfusion (rows: label, columns: predicted)")
    print("            " + " ".join(f"{level:>9s}" for level in LEVELS))
    for truth in LEVELS:
        print(f"  {truth:9s} " + " ".join(f"{cv['confusion'][truth][p]:9d}" for p in LEVELS))
    print("\nWeights of the final model (per severity):")
    print("  " + " " * 22 + " ".join(f"{level:>9s}" for level in LEVELS))
    for index, name in enumerate(FEATURES):
        print(f"  {name:22s}" + " ".join(f"{final.coef_[k][index]:9.2f}" for k in range(len(LEVELS))))
    print(f"\nWrote {OUTPUT}")


if __name__ == "__main__":
    main()
