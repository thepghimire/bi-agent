"""
Review sentiment scoring -- the NLP/transformers piece of this project.

order_reviews.review_comment_message is free-text Portuguese written by real
customers, which makes it a legitimate target for a transformer sentiment
model rather than a toy example. Two backends share one interface:

  - TransformerSentimentAnalyzer: a real multilingual transformer
    (nlptown/bert-base-multilingual-uncased-sentiment, a BERT fine-tuned for
    1-5 star sentiment across several languages including Portuguese) via
    the `transformers` pipeline API. This is the one you'd actually cite on
    a resume.
  - LexiconSentimentAnalyzer: a small, dependency-free Portuguese
    keyword-matching fallback. It exists so the rest of the system (the
    DB table, the batch scorer, the agent tool, the tests) keeps working
    in an environment with no internet access or without `transformers`/
    `torch` installed -- which is exactly the situation this was built and
    tested in. It is clearly a worse model and `score_reviews()` always
    records which backend produced a given row (review_sentiment.backend),
    so nothing is silently misrepresented as transformer output.

get_sentiment_analyzer("auto") tries the transformer first and falls back
to the lexicon with a printed warning -- "auto" is the default everywhere
in this project so the same code behaves well on a fully-equipped machine
and a locked-down one alike.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol

# -- Shared result type / interface ----------------------------------------


@dataclass(frozen=True)
class SentimentResult:
    label: str  # "positive" | "neutral" | "negative"
    score: float  # 0..1, confidence/strength
    backend: str  # "transformer" | "lexicon"


class SentimentAnalyzer(Protocol):
    backend_name: str

    def analyze(self, text: str) -> SentimentResult: ...


class SentimentBackendUnavailable(RuntimeError):
    """Raised when a requested backend can't be constructed (e.g. no internet
    to download transformer weights, or the optional packages aren't installed)."""


# -- Transformer backend -----------------------------------------------------

_TRANSFORMER_MODEL = "nlptown/bert-base-multilingual-uncased-sentiment"


class TransformerSentimentAnalyzer:
    """Wraps a HF `transformers` sentiment pipeline. Loading happens in
    __init__ (not lazily on first call) so failures surface immediately and
    get_sentiment_analyzer('auto') can fall back cleanly instead of failing
    midway through a batch."""

    backend_name = "transformer"

    def __init__(self, model_name: str = _TRANSFORMER_MODEL) -> None:
        try:
            from transformers import pipeline  # type: ignore
        except ImportError as exc:
            raise SentimentBackendUnavailable(
                "The 'transformers'/'torch' packages aren't installed. "
                "Run: pip install -r requirements-nlp.txt"
            ) from exc
        try:
            self._pipeline = pipeline("sentiment-analysis", model=model_name)
        except Exception as exc:  # network error, model-not-cached, etc.
            raise SentimentBackendUnavailable(
                f"Could not load transformer model '{model_name}' "
                f"(likely no internet access to huggingface.co on first use, "
                f"or the weights aren't cached locally yet): {exc}"
            ) from exc

    def analyze(self, text: str) -> SentimentResult:
        # nlptown's model returns labels like "1 star" .. "5 stars".
        result = self._pipeline(text[:512])[0]  # truncate -- reviews are short anyway
        stars = int(result["label"][0])
        if stars >= 4:
            label = "positive"
        elif stars == 3:
            label = "neutral"
        else:
            label = "negative"
        return SentimentResult(label=label, score=float(result["score"]), backend=self.backend_name)


# -- Lexicon fallback ---------------------------------------------------------

_POSITIVE_WORDS = {
    "bom", "boa", "otimo", "ótimo", "otima", "ótima", "excelente", "adorei", "amei",
    "recomendo", "perfeito", "perfeita", "rapido", "rápido", "rapida", "rápida",
    "show", "maravilhoso", "maravilhosa", "gostei", "satisfeito", "satisfeita",
    "qualidade", "sucesso", "feliz", "incrivel", "incrível", "top", "eficiente",
}
_NEGATIVE_WORDS = {
    "ruim", "pessimo", "péssimo", "pessima", "péssima", "horrivel", "horrível",
    "nao recomendo", "não recomendo", "demorou", "quebrado", "quebrada", "defeito",
    "errado", "errada", "atrasado", "atrasada", "cancelado", "cancelada", "problema",
    "insatisfeito", "insatisfeita", "enganacao", "enganação", "veio errado",
    "nao chegou", "não chegou", "nao gostei", "não gostei", "lixo", "decepcionado",
    "decepcionada",
}


class LexiconSentimentAnalyzer:
    """Deterministic, dependency-free keyword scorer. Deliberately simple --
    this is the safety-net backend, not the one doing the real work."""

    backend_name = "lexicon"

    def analyze(self, text: str) -> SentimentResult:
        normalized = (text or "").lower()
        pos = sum(1 for w in _POSITIVE_WORDS if w in normalized)
        neg = sum(1 for w in _NEGATIVE_WORDS if w in normalized)
        total = pos + neg
        if total == 0:
            return SentimentResult(label="neutral", score=0.5, backend=self.backend_name)
        if pos > neg:
            return SentimentResult(label="positive", score=pos / total, backend=self.backend_name)
        if neg > pos:
            return SentimentResult(label="negative", score=neg / total, backend=self.backend_name)
        return SentimentResult(label="neutral", score=0.5, backend=self.backend_name)


# -- Factory -------------------------------------------------------------


def get_sentiment_analyzer(backend: str = "auto") -> SentimentAnalyzer:
    if backend == "lexicon":
        return LexiconSentimentAnalyzer()
    if backend == "transformer":
        return TransformerSentimentAnalyzer()  # let it raise -- caller asked explicitly
    if backend != "auto":
        raise ValueError(f"Unknown sentiment backend '{backend}'. Use auto, transformer, or lexicon.")

    try:
        return TransformerSentimentAnalyzer()
    except SentimentBackendUnavailable as exc:
        print(f"[bi_agent.sentiment] falling back to lexicon backend: {exc}")
        return LexiconSentimentAnalyzer()


# -- Batch scoring over the DB -----------------------------------------------


def score_reviews(
    conn: sqlite3.Connection,
    analyzer: SentimentAnalyzer,
    limit: int = 200,
    rescore: bool = False,
) -> dict[str, int]:
    """Score up to `limit` reviews that have comment text, writing results
    into review_sentiment (upsert by review_id). By default skips reviews
    already scored, so re-running is cheap; rescore=True re-scores everyone
    (e.g. after switching backends).
    """
    where_scored = "" if rescore else "AND r.review_id NOT IN (SELECT review_id FROM review_sentiment)"
    rows = conn.execute(
        f"""
        SELECT r.review_id, r.review_comment_message
        FROM order_reviews r
        WHERE r.review_comment_message IS NOT NULL
          AND TRIM(r.review_comment_message) != ''
          {where_scored}
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    counts = {"scored": 0, "positive": 0, "neutral": 0, "negative": 0}
    for row in rows:
        result = analyzer.analyze(row["review_comment_message"])
        conn.execute(
            """
            INSERT INTO review_sentiment (review_id, label, score, backend)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(review_id) DO UPDATE SET
                label=excluded.label, score=excluded.score, backend=excluded.backend
            """,
            (row["review_id"], result.label, result.score, result.backend),
        )
        counts["scored"] += 1
        counts[result.label] += 1
    conn.commit()
    return counts
