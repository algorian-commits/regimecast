"""Sentiment models: FinBERT and one crypto-specific model, scored in batches.

Every model gives three probabilities (positive, negative, neutral). From them:

* ``label``  the most likely class,
* ``conf``   its probability,
* ``score``  P(positive) - P(negative), from -1 (very negative) to +1 (very positive).

The heavy libraries (torch, transformers) are imported only when a model is actually
loaded, so tests and the API run without them (``pip install -r requirements-ml.txt``).
"""
from __future__ import annotations

from typing import Callable, Iterable

import numpy as np
import pandas as pd

MODELS = {
    # Financial news, trained on Financial PhraseBank (Araci 2019).
    "finbert": "ProsusAI/finbert",
    # Crypto social-media posts (StockTwits, Telegram, Reddit), Bearish/Neutral/Bullish (Kulakowski & Frasincar 2023).
    "cryptobert": "ElKulako/cryptobert",
}
# each model's own class names -> ours
LABEL_MAP = {"positive": "positive", "negative": "negative", "neutral": "neutral",
             "bullish": "positive", "bearish": "negative"}
CLASSES = ["positive", "negative", "neutral"]

# probs(titles) -> array (n, 3) in CLASSES order
ProbFn = Callable[[list[str]], np.ndarray]


def load(name: str, device: str = "cpu", max_length: int = 64) -> ProbFn:
    """A function that turns a list of titles into class probabilities."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    repo = MODELS[name]
    tok = AutoTokenizer.from_pretrained(repo)
    model = AutoModelForSequenceClassification.from_pretrained(repo).to(device).eval()
    order = [CLASSES.index(LABEL_MAP[model.config.id2label[i].lower()]) for i in range(model.config.num_labels)]

    def probs(titles: list[str]) -> np.ndarray:
        enc = tok(titles, padding=True, truncation=True, max_length=max_length, return_tensors="pt").to(device)
        with torch.inference_mode():
            p = torch.softmax(model(**enc).logits, dim=-1).cpu().numpy()
        out = np.zeros((len(titles), 3))
        out[:, order] = p
        return out

    return probs


def score(titles: Iterable[str], probs: ProbFn, batch_size: int = 64, progress: bool = False) -> pd.DataFrame:
    """Columns ``p_pos, p_neg, p_neu, label, conf, score`` for each title, in order."""
    titles = list(titles)
    parts = []
    for i in range(0, len(titles), batch_size):
        parts.append(probs(titles[i:i + batch_size]))
        if progress and (i // batch_size) % 50 == 0:
            print(f"  {i + len(parts[-1])}/{len(titles)}", flush=True)
    p = np.vstack(parts) if parts else np.zeros((0, 3))
    return from_probs(p)


def from_probs(p: np.ndarray) -> pd.DataFrame:
    p = np.asarray(p, dtype=float).reshape(-1, 3)
    best = p.argmax(axis=1) if len(p) else np.zeros(0, dtype=int)
    return pd.DataFrame({
        "p_pos": p[:, 0], "p_neg": p[:, 1], "p_neu": p[:, 2],
        "label": [CLASSES[i] for i in best],
        "conf": p.max(axis=1) if len(p) else np.zeros(0),
        "score": p[:, 0] - p[:, 1],
    })
