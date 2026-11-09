"""
bi_agent
========

An agentic BI assistant: natural-language questions -> SQL -> results ->
charts -> stakeholder-readable summaries, over a real e-commerce dataset
(Olist, ~99k orders). Includes a transformer-based NLP tool for review
sentiment, since the review text in this dataset is free-form Portuguese.

    load_data.py -> SQLite  --(schema/run_sql_query/create_chart/sentiment tools)-->  LLM agent

See README.md for setup, usage, and the architecture/roadmap notes.
"""

__version__ = "0.1.0"
