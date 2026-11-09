# bi-agent

A text-to-SQL-and-chart business intelligence agent over a real e-commerce
dataset (Olist, ~99k Brazilian marketplace orders, 2016-2018), with a
transformer-based NLP tool for review sentiment. This is a separate,
standalone project from `football-agent` — same underlying idea (an LLM with
a small set of honest, schema-aware tools), applied to the domain that maps
most directly onto Data Analyst / BI / Data Engineer job postings.

```
raw_data/*.csv --load--> SQLite --(get_schema / run_sql_query / create_chart_from_query / sentiment tools)--> LLM agent
```

## Why this project

- **It's the real agentic-BI pattern, not a toy NL parser.** The agent isn't
  given a hand-written "translate English to SQL" function — it's given the
  schema and a guarded SQL-execution tool, and it writes the SQL itself.
  That's how production text-to-SQL agents actually work.
- **The SQL execution tool is defensive by design**, not just "it works":
  single-statement-only, SELECT/WITH-only, a keyword blocklist, a
  read-only SQLite connection at the OS level, and a hard row cap — four
  independent layers, documented in `bi_agent/query.py`.
- **The NLP is real, not decorative.** `order_reviews.review_comment_message`
  is free-text Portuguese written by actual customers. `bi_agent/sentiment.py`
  scores it with a multilingual transformer
  (`nlptown/bert-base-multilingual-uncased-sentiment`), with a small
  dependency-free lexicon fallback that kicks in automatically if there's no
  internet access to download model weights — this project was built and
  tested in exactly that locked-down environment, and the fallback is
  tracked per-row (`review_sentiment.backend`) so nothing is silently
  misrepresented as transformer output.
- **Charts follow an actual design system**, not matplotlib defaults — a
  validated, colorblind-checked categorical palette in fixed order, one hue
  for magnitude-only charts, a hard cap on series count (extras fold into
  "Other"), legends only when needed, no dual axes. See `bi_agent/charts.py`.

## Setup

```bash
pip install -r requirements.txt
python -m bi_agent.cli load-data
```

That's it for the core pipeline — `raw_data/*.csv` is already included (see
`raw_data/SOURCES.md` for exactly what's real data vs. what was
reconstructed, and the license).

## Usage

```bash
# What can I query?
python -m bi_agent.cli schema

# Raw SQL
python -m bi_agent.cli query "SELECT category_english, COUNT(*) AS n FROM v_order_items_full GROUP BY category_english ORDER BY n DESC LIMIT 5"

# SQL + chart in one step
python -m bi_agent.cli chart \
  --query "SELECT category_english, SUM(revenue) AS total FROM v_order_items_full GROUP BY category_english ORDER BY total DESC LIMIT 8" \
  --type hbar --x category_english --y total --title "Top categories by revenue"

# Score review sentiment (lexicon fallback works with zero extra installs;
# add requirements-nlp.txt for the real transformer)
python -m bi_agent.cli score-reviews --limit 300
python -m bi_agent.cli sentiment --category health_beauty
```

### Chat UI (Streamlit)

```bash
pip install -r requirements-agent.txt -r requirements-app.txt
streamlit run app.py
```

A browser chat over the same agent. Ask a question (or click one of the
example questions) and the app shows its work as it goes: each SQL query
the model wrote, the rows it got back, every chart it rendered, and the
written answer. Follow-up questions keep context ("now split that by
state"). The sidebar takes your API key if `ANTHROPIC_API_KEY` isn't set,
lets you pick the model, can build the database for you, and can score
review sentiment in batches of 1,000.

### Talking to it from the command line

```bash
pip install -r requirements-agent.txt
export ANTHROPIC_API_KEY=...
python agent_demo.py "Which product categories bring in the most revenue? Show me a chart."
python agent_demo.py "How do customers feel about the health_beauty category?"
```

Both front ends drive the same loop in `bi_agent/agent.py`: the model gets
`TOOLS`, decides which to call (and with what SQL), and
`TOOL_FUNCTIONS[name](**input)` runs it. No hand-written NL-to-SQL
translation layer — the model writes real SQL against the real schema.
The loop also keeps multi-turn history, turns tool failures into error
results the model can read and recover from (instead of crashing), and
stops after `max_steps` if the model keeps calling tools. Set
`BI_AGENT_MODEL` to change the default model.

## Project layout

```
bi_agent/
  config.py       settings from environment variables
  db.py           SQLite schema + the v_order_items_full convenience view
  load_data.py    ETL: raw_data/*.csv -> SQLite (full refresh)
  query.py        get_schema() + the guarded run_sql_query()
  charts.py       create_chart(): query results -> PNG, design-system-aware
  sentiment.py    transformer + lexicon-fallback review sentiment scoring
  tools.py        agent-facing tool functions + Anthropic tool-use schemas
  agent.py        the tool-calling loop (multi-turn, error-tolerant, step-capped)
  cli.py          command-line entry point
raw_data/         the Olist CSVs + SOURCES.md (what's real, what's reconstructed, license)
tests/            55 pytest tests, all offline (a scripted fake model stands in for the API)
app.py            Streamlit chat UI
agent_demo.py     command-line version of the same agent
```

## Run the tests

```bash
pytest -v
```

55 tests, no network or API key required — they run against small
hand-crafted fixture data (see `tests/conftest.py`), not the full 99k-row
dataset, so they're fast and the expected numbers are exact. The agent
loop and the Streamlit app are tested end to end with a scripted fake
model (`tests/fake_llm.py`) that plays back tool calls and answers.

## A note on the sentiment backend

By default (`BI_AGENT_SENTIMENT_BACKEND=auto`), `score-reviews` tries to load
the transformer model and silently falls back to the lexicon scorer if it
can't (no `transformers`/`torch` installed, or no internet access to
huggingface.co). On a normal machine with internet access, just:

```bash
pip install -r requirements-nlp.txt
python -m bi_agent.cli score-reviews --backend transformer --limit 500
```

The first call downloads the model (~650MB) and caches it; after that it's
fast. `--backend lexicon` forces the fallback explicitly if you want a
quick, dependency-free demo.

## Roadmap: where this goes next

1. **More tools on the same data**: `get_delivery_performance()` (on-time
   vs. late, by state/seller), `compare_periods(metric, period_a, period_b)`,
   a basic anomaly flag ("this category's revenue dropped >20% month over
   month").
2. **Smarter sentiment**: fine-tune on this dataset's own review_score as
   weak labels instead of relying on a general-purpose multilingual model;
   add aspect-based sentiment (delivery speed vs. product quality vs. price)
   instead of one overall label.
3. **Scheduled refresh**: wrap `load-data` behind a cron job / Workflow
   trigger so the "BI" story includes freshness, not just a one-time load
   (this dataset is static, but the pattern is the point).
4. **Multi-agent**: split into a "data agent" (schema/SQL/chart) and an
   "insight agent" that reviews the data agent's output and writes the
   stakeholder-facing narrative, rather than one agent doing both.
5. **Reconnect to the original framework idea**: `football-agent` and
   `bi-agent` currently stand alone on purpose (see the project history —
   keeping them separate kept each one gradeable on its own for a course
   deadline). The natural next step, if there's time, is a thin orchestrator
   that holds both tool sets and a database (or domain) registry so asking
   about a demo e-commerce channel and asking about Arsenal's form go
   through the same front door — the "chat with any dataset" framework
   both of these were built to feed into.

## Data & license

See `raw_data/SOURCES.md`. Short version: real Olist e-commerce data under
CC BY-NC-SA 4.0 (non-commercial, attribution, share-alike), used here for a
non-commercial course project / portfolio piece; geolocation data is
intentionally omitted (not needed for anything this project answers); the
category-name English translation is an original glossary written for this
project, not scraped.
