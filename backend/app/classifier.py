from __future__ import annotations

import json
from functools import lru_cache
from math import exp
from pathlib import Path
from typing import Dict, List, Tuple


BASE_DIR = Path(__file__).resolve().parents[2]
MODEL_DIR = BASE_DIR / "backend" / "model"
LABELS_FILE = MODEL_DIR / "labels.json"

LABEL_RISK_WEIGHTS: Dict[str, float] = {
    "self_harm": 100.0,
    "violence": 72.0,
    "depression": 50.0,
    "anxiety": 36.0,
    "academic_stress": 24.0,
    "social_withdrawal": 24.0,
    "abuse": 22.0,
}

DEFAULT_THRESHOLDS: Dict[str, float] = {
    "self_harm": 0.55,
    "violence": 0.6,
    "depression": 0.5,
    "anxiety": 0.5,
    "academic_stress": 0.5,
    "social_withdrawal": 0.5,
    "abuse": 0.6,
}


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)
    z = exp(value)
    return z / (1.0 + z)


@lru_cache(maxsize=1)
def _load_labels() -> List[str]:
    if not LABELS_FILE.exists():
        raise RuntimeError(f"Model labels file not found: {LABELS_FILE}")
    payload = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    labels = payload.get("labels") or []
    if not labels:
        raise RuntimeError(f"Model labels file is empty: {LABELS_FILE}")
    return list(labels)


@lru_cache(maxsize=1)
def _load_model_bundle():
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "Classifier runtime dependencies are missing. Install backend/requirements.txt first."
        ) from exc

    if not MODEL_DIR.exists():
        raise RuntimeError(f"Model directory not found: {MODEL_DIR}")

    tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR), local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(str(MODEL_DIR), local_files_only=True)
    model.eval()
    return torch, tokenizer, model


def classify_text(text: str) -> Tuple[List[str], str, float, List[str], float]:
    normalized_text = str(text or "").strip()
    if not normalized_text:
        return ["normal"], "low", 0.0, [], 0.0

    labels_order = _load_labels()
    torch, tokenizer, model = _load_model_bundle()
    encoded = tokenizer(
        normalized_text,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )

    with torch.no_grad():
        logits = model(**encoded).logits[0].detach().cpu().tolist()

    probabilities = {
        label: round(_sigmoid(float(logit)), 4)
        for label, logit in zip(labels_order, logits)
    }

    labels = [
        label
        for label in labels_order
        if probabilities.get(label, 0.0) >= DEFAULT_THRESHOLDS.get(label, 0.5)
    ]
    if not labels:
        labels = ["normal"]

    weighted_score = sum(
        probabilities[label] * LABEL_RISK_WEIGHTS.get(label, 0.0)
        for label in labels
        if label != "normal"
    )
    score = round(min(weighted_score, 100.0), 1)

    if "self_harm" in labels or score >= 56:
        risk_level = "high"
    elif score >= 22:
        risk_level = "medium"
    else:
        risk_level = "low"

    matched_labels = [
        f"model:{label}:{probabilities[label]:.2f}"
        for label in labels_order
        if probabilities.get(label, 0.0) >= 0.30
    ]
    confidence = max((probabilities.get(label, 0.0) for label in labels_order), default=0.0)
    return labels, risk_level, score, matched_labels, round(confidence, 2)
