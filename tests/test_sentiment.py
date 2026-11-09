import pytest

from bi_agent.sentiment import (
    LexiconSentimentAnalyzer,
    get_sentiment_analyzer,
    score_reviews,
)


def test_lexicon_analyzer_detects_positive():
    analyzer = LexiconSentimentAnalyzer()
    result = analyzer.analyze("Otimo produto, adorei, recomendo demais!")
    assert result.label == "positive"
    assert result.backend == "lexicon"


def test_lexicon_analyzer_detects_negative():
    analyzer = LexiconSentimentAnalyzer()
    result = analyzer.analyze("Pessimo, produto quebrado, nao chegou")
    assert result.label == "negative"


def test_lexicon_analyzer_neutral_on_no_signal():
    analyzer = LexiconSentimentAnalyzer()
    result = analyzer.analyze("O produto chegou na caixa marrom padrao")
    assert result.label == "neutral"


def test_get_sentiment_analyzer_explicit_lexicon():
    analyzer = get_sentiment_analyzer("lexicon")
    assert analyzer.backend_name == "lexicon"


def test_get_sentiment_analyzer_rejects_unknown_backend():
    with pytest.raises(ValueError):
        get_sentiment_analyzer("not_a_real_backend")


def test_get_sentiment_analyzer_auto_returns_a_working_analyzer():
    # Whichever backend "auto" resolves to in this environment, it must work.
    analyzer = get_sentiment_analyzer("auto")
    result = analyzer.analyze("Otimo, recomendo!")
    assert result.label in ("positive", "neutral", "negative")
    assert 0.0 <= result.score <= 1.0


def test_score_reviews_writes_and_is_idempotent(seeded_conn):
    analyzer = LexiconSentimentAnalyzer()
    counts = score_reviews(seeded_conn, analyzer, limit=10)
    assert counts["scored"] == 2
    assert counts["positive"] == 1
    assert counts["negative"] == 1

    # second run with rescore=False should skip already-scored reviews
    counts_again = score_reviews(seeded_conn, analyzer, limit=10)
    assert counts_again["scored"] == 0

    total = seeded_conn.execute("SELECT COUNT(*) AS c FROM review_sentiment").fetchone()["c"]
    assert total == 2


def test_score_reviews_rescore_overwrites(seeded_conn):
    analyzer = LexiconSentimentAnalyzer()
    score_reviews(seeded_conn, analyzer, limit=10)
    counts = score_reviews(seeded_conn, analyzer, limit=10, rescore=True)
    assert counts["scored"] == 2  # re-scored, not skipped
