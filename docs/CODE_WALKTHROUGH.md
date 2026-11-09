# BI Agent code walkthrough

How to debug the project, the path one question takes through the code, and every file explained section by section. Line numbers match the code at the time this was written.

## Debugging

- **VS Code:** open the folder, select the `.venv` interpreter, open Run and Debug (Ctrl+Shift+D), pick a configuration from `.vscode/launch.json`, press F5.
  - *Streamlit app (debug)*: the full app with the debugger attached.
  - *One agent turn, offline*: `scripts/debug_agent.py --offline`, a scripted fake model against the real database. No API key, no cost.
  - *One agent turn, real Claude*: same script with the real model; saves the conversation to `debug_last_turn.json`.
  - *Current test file*: pytest on the open file.
- Keys: F10 step over, F11 step into, Shift+F11 step out, F5 continue.
- Streamlit reruns `app.py` from the top on every interaction; state that must survive lives in `st.session_state`.
- `ANTHROPIC_LOG=debug` in `.env` logs raw API requests. `breakpoint()` + pdb works without an IDE.

## One question, step by step

| # | Where | What happens |
|---|---|---|
| 1 | `app.py:213` | You type a question; `st.chat_input` returns it on this rerun. |
| 2 | `app.py:252` | The app calls `run_agent()` with your question, the earlier history and the client. |
| 3 | `bi_agent/agent.py:134` | The question is appended to the message history. |
| 4 | `bi_agent/agent.py:138` | Request 1 goes to Claude: system prompt, tools, messages. |
| 5 | `bi_agent/agent.py:146` | Claude replies with `stop_reason = "tool_use"`: it wants `get_schema`. |
| 6 | `bi_agent/agent.py:99` | `execute_tool` calls `tools.get_schema()`. |
| 7 | `bi_agent/query.py:90` | `PRAGMA table_info` reads each table's columns. |
| 8 | `bi_agent/agent.py:168` | The schema goes back to Claude as a tool result; the loop repeats. |
| 9 | `bi_agent/agent.py:99` | Claude now asks for `create_chart_from_query` with SQL it wrote. |
| 10 | `bi_agent/tools.py:72` | The chart tool runs the SQL first. |
| 11 | `bi_agent/query.py:118` | `validate_query` checks the SQL (layers 1 and 2). |
| 12 | `bi_agent/query.py:130` | SQLite runs it on a read-only connection (layer 3), capped at 500 rows (layer 4). |
| 13 | `bi_agent/charts.py:137` | The rows are drawn with matplotlib. |
| 14 | `bi_agent/charts.py:143` | The PNG is saved to `charts_out/`. |
| 15 | `bi_agent/agent.py:158` | The loop reads the PNG bytes into the ToolCall. |
| 16 | `app.py:249` | `on_end` draws the step inside the progress box. |
| 17 | `bi_agent/agent.py:148` | Claude replies with text and `end_turn`; `run_agent` returns an AgentTurn. |
| 18 | `app.py:266` | The app shows the chart and the answer. |
| 19 | `app.py:269` | History is saved in session state for follow-up questions. |

## `bi_agent/config.py`

Settings. Every other module asks this file where things live.

### Imports and loading .env (lines 1–10)

```python
 1  """Configuration for bi_agent, read from environment variables (.env supported)."""
 2  from __future__ import annotations
 3  
 4  import os
 5  from dataclasses import dataclass
 6  from pathlib import Path
 7  
 8  from dotenv import load_dotenv
 9  
10  load_dotenv()
```

`load_dotenv()` on line 10 runs **once, the first time anything imports `bi_agent.config`**. It reads the `.env` file in the folder you launched from and copies each `KEY=value` into `os.environ`. That's how your `ANTHROPIC_API_KEY` reaches the Anthropic SDK without the code ever reading it explicitly.

It does not overwrite variables that are already set, so a key exported in your terminal wins over the one in `.env`.

### Default paths (lines 12–15)

```python
12  PROJECT_ROOT = Path(__file__).resolve().parent.parent
13  DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "bi_agent.db"
14  DEFAULT_RAW_DATA_DIR = PROJECT_ROOT / "raw_data"
15  DEFAULT_CHARTS_DIR = PROJECT_ROOT / "charts_out"
```

`Path(__file__)` is this file (`bi_agent/config.py`). `.resolve().parent.parent` climbs two levels to the project root. Every default path is built from that root, so the project works no matter which folder you launch it from.

### The Settings dataclass (lines 18–24)

```python
18  @dataclass(frozen=True)
19  class Settings:
20      db_path: Path
21      raw_data_dir: Path
22      charts_dir: Path
23      max_query_rows: int
24      sentiment_backend: str  # "auto" | "transformer" | "lexicon"
```

A plain container for five values. `frozen=True` makes it read-only: code can't accidentally change a setting halfway through a run.

### get_settings() (lines 27–44)

```python
27  def get_settings() -> Settings:
28      """
29      BI_AGENT_DB_PATH          -- where the SQLite database lives
30      BI_AGENT_RAW_DATA_DIR     -- folder with the Olist CSVs (see raw_data/SOURCES.md)
31      BI_AGENT_CHARTS_DIR       -- where chart PNGs are written
32      BI_AGENT_MAX_QUERY_ROWS   -- hard cap on rows any tool query can return
33      BI_AGENT_SENTIMENT_BACKEND -- "auto" tries a transformer model and falls
34          back to a small lexicon scorer if it can't be loaded (no internet,
35          package missing, etc). Force one explicitly with "transformer" or
36          "lexicon".
37      """
38      return Settings(
39          db_path=Path(os.getenv("BI_AGENT_DB_PATH", str(DEFAULT_DB_PATH))),
40          raw_data_dir=Path(os.getenv("BI_AGENT_RAW_DATA_DIR", str(DEFAULT_RAW_DATA_DIR))),
41          charts_dir=Path(os.getenv("BI_AGENT_CHARTS_DIR", str(DEFAULT_CHARTS_DIR))),
42          max_query_rows=int(os.getenv("BI_AGENT_MAX_QUERY_ROWS", "500")),
43          sentiment_backend=os.getenv("BI_AGENT_SENTIMENT_BACKEND", "auto"),
44      )
```

Reads each setting from an environment variable, falling back to the default. `os.getenv("NAME", default)` returns the variable if set, else the default.

Note that this is a function, not a module-level constant. Every call re-reads the environment, which is why the tests can point `BI_AGENT_DB_PATH` at a temporary database with `monkeypatch.setenv` and everything just follows.

> **Breakpoint, line 38:** Inspect the returned values to confirm which database and chart folder the app is really using.


## `bi_agent/db.py`

The database schema: table definitions, indexes and the one view the agent uses most.

### Module docstring and imports (lines 1–17)

```python
 1  """
 2  SQLite schema for the Olist e-commerce data, plus one convenience view.
 3  
 4  Design note: `v_order_items_full` flattens order_items + orders + products +
 5  category translation + customers + sellers into one wide fact table. Real
 6  BI tools almost always give an agent (or an analyst) a small number of wide,
 7  pre-joined views rather than forcing every question through a 6-table join
 8  -- it's the difference between the agent writing
 9  
10      SELECT category, SUM(revenue) FROM v_order_items_full GROUP BY category
11  
12  and re-deriving the same 6-way join from scratch on every question.
13  """
14  from __future__ import annotations
15  
16  import sqlite3
17  from pathlib import Path
```

The docstring explains the main design decision: give the agent one wide, pre-joined view so it writes a short `GROUP BY` instead of a six-table join.

### Lookup tables: customers, sellers, categories, products (lines 19–50)

```python
19  SCHEMA = """
20  CREATE TABLE IF NOT EXISTS customers (
21      customer_id TEXT PRIMARY KEY,
22      customer_unique_id TEXT,
23      customer_zip_code_prefix TEXT,
24      customer_city TEXT,
25      customer_state TEXT
26  );
27  
28  CREATE TABLE IF NOT EXISTS sellers (
29      seller_id TEXT PRIMARY KEY,
30      seller_zip_code_prefix TEXT,
31      seller_city TEXT,
32      seller_state TEXT
33  );
34  
35  CREATE TABLE IF NOT EXISTS category_translation (
36      product_category_name TEXT PRIMARY KEY,
37      product_category_name_english TEXT
38  );
39  
40  CREATE TABLE IF NOT EXISTS products (
41      product_id TEXT PRIMARY KEY,
42      product_category_name TEXT,
43      product_name_lenght REAL,
44      product_description_lenght REAL,
45      product_photos_qty REAL,
46      product_weight_g REAL,
47      product_length_cm REAL,
48      product_height_cm REAL,
49      product_width_cm REAL
50  );
```

`SCHEMA` is one big SQL string, run in one go by `executescript()` in `connect()` below.

`CREATE TABLE IF NOT EXISTS` makes it safe to run every time: it only creates what's missing. Note the misspelled `product_name_lenght` columns: they're spelled that way in the original Olist CSV, and the column names must match for pandas to load them.

### orders (lines 52–64)

```python
52  CREATE TABLE IF NOT EXISTS orders (
53      order_id TEXT PRIMARY KEY,
54      customer_id TEXT NOT NULL,
55      order_status TEXT,
56      order_purchase_timestamp TEXT,
57      order_approved_at TEXT,
58      order_delivered_carrier_date TEXT,
59      order_delivered_customer_date TEXT,
60      order_estimated_delivery_date TEXT,
61      FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
62  );
63  CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders (customer_id);
64  CREATE INDEX IF NOT EXISTS idx_orders_purchase_ts ON orders (order_purchase_timestamp);
```

One row per order. All the timestamps are stored as TEXT (`'2017-10-02 10:56:33'`). SQLite has no real date type; ISO-formatted text sorts correctly and works with `strftime()` and `julianday()`, which the model uses for monthly trends and delivery delays.

The two indexes speed up the most common lookups: joining orders to customers and filtering by purchase date.

### order_items, order_payments, order_reviews (lines 66–103)

```python
 66  CREATE TABLE IF NOT EXISTS order_items (
 67      order_id TEXT NOT NULL,
 68      order_item_id INTEGER NOT NULL,
 69      product_id TEXT,
 70      seller_id TEXT,
 71      price REAL,
 72      freight_value REAL,
 73      PRIMARY KEY (order_id, order_item_id),
 74      FOREIGN KEY (order_id) REFERENCES orders (order_id),
 75      FOREIGN KEY (product_id) REFERENCES products (product_id),
 76      FOREIGN KEY (seller_id) REFERENCES sellers (seller_id)
 77  );
 78  CREATE INDEX IF NOT EXISTS idx_items_order ON order_items (order_id);
 79  CREATE INDEX IF NOT EXISTS idx_items_product ON order_items (product_id);
 80  
 81  CREATE TABLE IF NOT EXISTS order_payments (
 82      order_id TEXT NOT NULL,
 83      payment_sequential INTEGER NOT NULL,
 84      payment_type TEXT,
 85      payment_installments INTEGER,
 86      payment_value REAL,
 87      PRIMARY KEY (order_id, payment_sequential),
 88      FOREIGN KEY (order_id) REFERENCES orders (order_id)
 89  );
 90  CREATE INDEX IF NOT EXISTS idx_payments_order ON order_payments (order_id);
 91  
 92  CREATE TABLE IF NOT EXISTS order_reviews (
 93      review_id TEXT,
 94      order_id TEXT NOT NULL,
 95      review_score INTEGER,
 96      review_comment_title TEXT,
 97      review_comment_message TEXT,
 98      review_creation_date TEXT,
 99      review_answer_timestamp TEXT,
100      PRIMARY KEY (review_id, order_id),
101      FOREIGN KEY (order_id) REFERENCES orders (order_id)
102  );
103  CREATE INDEX IF NOT EXISTS idx_reviews_order ON order_reviews (order_id);
```

These are the "many" side of each order. One order can have several items (possibly from different sellers), several payments, and a review.

Composite primary keys like `(order_id, order_item_id)` mean "unique together". `FOREIGN KEY` lines document how tables connect. They are only enforced when `PRAGMA foreign_keys = ON` (line 146).

### review_sentiment (lines 105–112)

```python
105  -- Populated separately by the sentiment tool (bi_agent.sentiment), keyed by
106  -- review_id so re-running analysis is an upsert, not a re-score-everything.
107  CREATE TABLE IF NOT EXISTS review_sentiment (
108      review_id TEXT PRIMARY KEY,
109      label TEXT NOT NULL,       -- 'positive' | 'neutral' | 'negative'
110      score REAL NOT NULL,       -- confidence/strength, 0..1
111      backend TEXT NOT NULL      -- 'transformer' | 'lexicon'
112  );
```

Empty after loading. The NLP step fills it (see `sentiment.py`). It's keyed by `review_id`, so scoring the same review again replaces the row instead of duplicating it. The `backend` column records which model produced each row.

### The v_order_items_full view (lines 114–139)

```python
114  CREATE VIEW IF NOT EXISTS v_order_items_full AS
115  SELECT
116      oi.order_id,
117      oi.order_item_id,
118      oi.price,
119      oi.freight_value,
120      (oi.price + oi.freight_value) AS revenue,
121      o.order_status,
122      o.order_purchase_timestamp,
123      o.order_delivered_customer_date,
124      o.order_estimated_delivery_date,
125      p.product_id,
126      p.product_category_name,
127      COALESCE(ct.product_category_name_english, p.product_category_name) AS category_english,
128      s.seller_id,
129      s.seller_state,
130      c.customer_id,
131      c.customer_state,
132      c.customer_city
133  FROM order_items oi
134  JOIN orders o ON o.order_id = oi.order_id
135  JOIN customers c ON c.customer_id = o.customer_id
136  LEFT JOIN products p ON p.product_id = oi.product_id
137  LEFT JOIN category_translation ct ON ct.product_category_name = p.product_category_name
138  LEFT JOIN sellers s ON s.seller_id = oi.seller_id;
139  """
```

The most important SQL in the project. One row per order item, with the order, product, English category, seller and customer joined on, plus a computed `revenue = price + freight_value`.

- `JOIN` (inner) for orders and customers: every item must have both.
- `LEFT JOIN` for products, categories and sellers: keep the item even if a lookup is missing.
- `COALESCE(english, portuguese)` uses the English category name when one exists, otherwise the original Portuguese.

A view stores no data. SQLite runs this query each time the view is read, so it always reflects the current tables.

### connect() (lines 142–148)

```python
142  def connect(db_path: Path) -> sqlite3.Connection:
143      db_path.parent.mkdir(parents=True, exist_ok=True)
144      conn = sqlite3.connect(db_path)
145      conn.row_factory = sqlite3.Row
146      conn.execute("PRAGMA foreign_keys = ON")
147      conn.executescript(SCHEMA)
148      return conn
```

Opens (or creates) the database file and returns a connection.

- Line 143 creates the `data/` folder if it's missing.
- Line 145, `row_factory = sqlite3.Row`, makes rows accessible by column name (`row["label"]`) instead of only by position.
- Line 147 runs the whole schema, so any connection is guaranteed to have every table.

This is the **read-write** connection, used for loading and scoring. The agent's queries go through a separate read-only connection in `query.py`.

### QUERYABLE_OBJECTS (lines 151–165)

```python
151  # Tables an agent is allowed to query / describe. Deliberately a fixed list
152  # (not "whatever's in sqlite_master") so a future migration table or scratch
153  # table doesn't silently become queryable by the LLM.
154  QUERYABLE_OBJECTS = [
155      "customers",
156      "sellers",
157      "category_translation",
158      "products",
159      "orders",
160      "order_items",
161      "order_payments",
162      "order_reviews",
163      "review_sentiment",
164      "v_order_items_full",
165  ]
```

A fixed allow-list of what the agent may see in `get_schema`. If you add a scratch table later, it stays invisible to the model unless you add it here on purpose.


## `bi_agent/load_data.py`

ETL: reads the 8 CSVs and loads them into SQLite.

### Docstring and imports (lines 1–19)

```python
 1  """
 2  ETL: raw Olist CSVs (raw_data/) -> SQLite (bi_agent.db).
 3  
 4  This is a full-refresh loader (truncate + reload), which is the right model
 5  for a mostly-static historical dataset like this one -- there's no live API
 6  to incrementally sync against, unlike the football-agent pipeline. Each
 7  `load_*` function is one table so a failure on one file doesn't require
 8  re-reading the others, and so each step is independently testable.
 9  """
10  from __future__ import annotations
11  
12  import sqlite3
13  from pathlib import Path
14  from typing import Any
15  
16  import pandas as pd
17  
18  from . import db
19  from .config import Settings, get_settings
```

A full-refresh loader: every run deletes and reloads each table. That suits a static historical dataset.

### Load order and file names (lines 21–42)

```python
21  # Order matters: children before parents would violate foreign keys.
22  LOAD_ORDER = [
23      "customers",
24      "sellers",
25      "category_translation",
26      "products",
27      "orders",
28      "order_items",
29      "order_payments",
30      "order_reviews",
31  ]
32  
33  _FILENAMES = {
34      "customers": "olist_customers_dataset.csv",
35      "sellers": "olist_sellers_dataset.csv",
36      "category_translation": "product_category_name_translation.csv",
37      "products": "olist_products_dataset.csv",
38      "orders": "olist_orders_dataset.csv",
39      "order_items": "olist_order_items_dataset.csv",
40      "order_payments": "olist_order_payments_dataset.csv",
41      "order_reviews": "olist_order_reviews_dataset.csv",
42  }
```

`LOAD_ORDER` lists parent tables before child tables (customers before orders, orders before items). `_FILENAMES` maps each table to its CSV.

### _load_table(): one CSV into one table (lines 45–60)

```python
45  def _load_table(conn: sqlite3.Connection, raw_data_dir: Path, table: str) -> int:
46      csv_path = raw_data_dir / _FILENAMES[table]
47      if not csv_path.exists():
48          raise FileNotFoundError(
49              f"Missing {csv_path}. See raw_data/SOURCES.md for where this file comes from."
50          )
51      df = pd.read_csv(csv_path)
52  
53      if table == "order_reviews":
54          # The raw file has a handful of (review_id, order_id) duplicates;
55          # our primary key is on that pair, so de-dupe before inserting.
56          df = df.drop_duplicates(subset=["review_id", "order_id"])
57  
58      conn.execute(f"DELETE FROM {table}")
59      df.to_sql(table, conn, if_exists="append", index=False)
60      return len(df)
```

- Line 51: pandas reads the whole CSV into a DataFrame.
- Lines 53-56: the raw reviews file contains a few duplicate `(review_id, order_id)` pairs, which would break the primary key, so they're dropped first. That's why `order_reviews` has exactly 100,000 rows.
- Line 58 empties the table; line 59 bulk-inserts the DataFrame. `if_exists="append"` keeps the table definition from `db.py` instead of letting pandas invent its own column types.

> **Breakpoint, line 51:** Step over line 51, then inspect `df.shape` and `df.head()` to see the raw data for each table.

### load_all() (lines 63–82)

```python
63  def load_all(settings: Settings | None = None) -> dict[str, Any]:
64      settings = settings or get_settings()
65      conn = db.connect(settings.db_path)
66      counts: dict[str, int] = {}
67      try:
68          # Foreign keys off during bulk load -- children can be re-inserted
69          # before their parents are re-populated without tripping FK checks
70          # mid-refresh; schema integrity is still enforced at table-creation
71          # time and by the application code, just not instant-by-instant here.
72          conn.execute("PRAGMA foreign_keys = OFF")
73          for table in LOAD_ORDER:
74              counts[table] = _load_table(conn, settings.raw_data_dir, table)
75          conn.commit()
76      finally:
77          conn.close()
78      return {
79          "db_path": str(settings.db_path),
80          "tables_loaded": counts,
81          "total_rows": sum(counts.values()),
82      }
```

Opens a connection, turns foreign-key checks off for the bulk load (line 72), loads each table in order, and commits once at the end (line 75). One commit means a crash midway leaves the old data untouched.

`try/finally` guarantees the connection closes even if a CSV is missing. The returned dict is what you see printed after `cli load-data`.

### Running the file directly (lines 85–88)

```python
85  if __name__ == "__main__":
86      import json
87  
88      print(json.dumps(load_all(), indent=2))
```

Lets you run `python -m bi_agent.load_data` as well as the CLI command.


## `bi_agent/query.py`

The two tools that touch the database for the agent: describing the schema and running SQL safely.

### Docstring and imports (lines 1–32)

```python
 1  """
 2  Schema introspection and a guarded, read-only SQL execution tool.
 3  
 4  This is the part of an agentic BI system that actually has to be defensive:
 5  an LLM is going to write arbitrary SQL text and we're going to run it. The
 6  guardrails here are layered on purpose -- no single check is trusted alone:
 7  
 8    1. Statement shape: must be exactly one SELECT/WITH statement (no chained
 9       statements via ';', no comments hiding a second statement).
10    2. Keyword blocklist: reject any write/DDL/pragma/attach keyword appearing
11       anywhere in the text, not just at the start (catches subqueries like
12       `SELECT * FROM x; DROP TABLE y` as well as `SELECT (SELECT ... ATTACH ...)`).
13    3. The connection itself is opened read-only at the OS/SQLite level
14       (`mode=ro` + `PRAGMA query_only=ON`), so even a guard that missed
15       something still can't write.
16    4. A hard row cap, enforced in Python after fetch, independent of
17       whatever LIMIT (if any) the query itself specified.
18  
19  Defense in depth: if you only had step 3, a crafted query could still hang
20  the process reading millions of rows; if you only had steps 1-2, a bug in
21  the regex is a straight path to disaster. Layering means one weak link
22  doesn't sink the tool.
23  """
24  from __future__ import annotations
25  
26  import re
27  import sqlite3
28  from pathlib import Path
29  from typing import Any
30  
31  from .config import Settings, get_settings
32  from .db import QUERYABLE_OBJECTS
```

The docstring lists the four safety layers. The code below implements them in order.

### The two regular expressions (lines 34–39)

```python
34  _ALLOWED_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
35  _FORBIDDEN_KEYWORDS = re.compile(
36      r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|DETACH|PRAGMA|VACUUM|REPLACE|"
37      r"CREATE|TRIGGER|REINDEX|ANALYZE|BEGIN|COMMIT|ROLLBACK|SAVEPOINT)\b",
38      re.IGNORECASE,
39  )
```

- `_ALLOWED_START`: the query must begin with `SELECT` or `WITH` (after optional spaces). `\b` is a word boundary, so `SELECTED` doesn't count.
- `_FORBIDDEN_KEYWORDS`: any of these words **anywhere** in the query gets it rejected.

`re.IGNORECASE` catches `drop`, `Drop` and `DROP` alike.

### TABLE_DESCRIPTIONS (lines 41–56)

```python
41  TABLE_DESCRIPTIONS = {
42      "customers": "One row per customer. customer_unique_id identifies the same person across repeat orders.",
43      "sellers": "One row per seller (marketplace vendor).",
44      "category_translation": "Maps product_category_name (Portuguese) to English.",
45      "products": "One row per product, including category and physical dimensions.",
46      "orders": "One row per order: status and the full timestamp lifecycle (purchase -> approved -> delivered).",
47      "order_items": "One row per line item within an order (an order can have multiple items/sellers).",
48      "order_payments": "One row per payment installment/method applied to an order.",
49      "order_reviews": "One row per customer review: review_score (1-5) and free-text comments in Portuguese.",
50      "review_sentiment": "Sentiment score for order_reviews.review_comment_message, populated by bi_agent.sentiment.",
51      "v_order_items_full": (
52          "Wide, pre-joined view: order_items + orders + products + category_translation "
53          "+ customers + sellers, with a computed `revenue` column (price + freight_value). "
54          "Start here for most business questions instead of re-joining from scratch."
55      ),
56  }
```

Plain-English hints sent to the model with the schema. These matter more than they look: the model reads them to decide which table to use. The `v_order_items_full` description is what steers it toward the view.

### QueryError (lines 59–60)

```python
59  class QueryError(ValueError):
60      """Raised when a query is rejected before ever touching the database."""
```

A custom exception type, so the code can catch "rejected by our checks" separately from real database errors.

### validate_query(): safety layers 1 and 2 (lines 63–80)

```python
63  def validate_query(sql: str) -> str:
64      """Raise QueryError with a specific reason, or return the cleaned SQL."""
65      cleaned = sql.strip()
66      if not cleaned:
67          raise QueryError("Empty query.")
68  
69      # Allow at most one trailing semicolon; reject anything after it.
70      body = cleaned[:-1].strip() if cleaned.endswith(";") else cleaned
71      if ";" in body:
72          raise QueryError("Only a single statement is allowed (no ';' inside the query).")
73  
74      if not _ALLOWED_START.match(body):
75          raise QueryError("Only SELECT (or WITH ... SELECT) statements are allowed.")
76  
77      if _FORBIDDEN_KEYWORDS.search(body):
78          raise QueryError("Query contains a disallowed keyword (writes/DDL/pragma are not permitted).")
79  
80      return body
```

Runs before the database is touched.

- Lines 65-67: reject an empty query.
- Lines 69-72: allow one trailing `;`, but any `;` inside means a second statement was chained on (`SELECT 1; DROP TABLE orders`), so reject it.
- Lines 74-75: must start with SELECT/WITH.
- Lines 77-78: no forbidden keywords anywhere.

Each failure raises `QueryError` with a specific message. That message is what the model reads, so it knows what to fix.

> **Breakpoint, line 70:** Ask the agent to delete something and watch which check rejects it.

### get_schema() (lines 83–100)

```python
 83  def get_schema(settings: Settings | None = None) -> dict[str, Any]:
 84      """Describe every table/view an agent is allowed to query."""
 85      settings = settings or get_settings()
 86      conn = sqlite3.connect(settings.db_path)
 87      try:
 88          tables = []
 89          for name in QUERYABLE_OBJECTS:
 90              cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
 91              tables.append(
 92                  {
 93                      "name": name,
 94                      "description": TABLE_DESCRIPTIONS.get(name, ""),
 95                      "columns": [{"name": c[1], "type": c[2]} for c in cols],
 96                  }
 97              )
 98          return {"tables": tables}
 99      finally:
100          conn.close()
```

For each allowed table, `PRAGMA table_info(name)` returns one row per column; `c[1]` is the column name and `c[2]` its type. The result is a plain dict, which gets turned into JSON for the model.

> **Breakpoint, line 98:** Inspect `tables` to see exactly what the model is told about the database.

### run_sql_query(), part 1: validation (lines 103–120)

```python
103  def run_sql_query(
104      query: str,
105      settings: Settings | None = None,
106      max_rows: int | None = None,
107  ) -> dict[str, Any]:
108      """Run a validated, read-only SELECT and return rows as plain dicts.
109  
110      Returns an 'error' key instead of raising, so this is safe to call
111      directly from an LLM tool-calling loop -- a rejected or failing query
112      becomes a message the model can read and react to, not a crash.
113      """
114      settings = settings or get_settings()
115      cap = max_rows or settings.max_query_rows
116  
117      try:
118          body = validate_query(query)
119      except QueryError as exc:
120          return {"error": str(exc)}
```

Every tool function in this project **returns** an error dict instead of raising. That's a deliberate pattern: an exception would stop the agent loop, but `{"error": "..."}` goes back to the model as a normal result, and the model can fix its SQL and try again.

`cap` is the row limit, 500 by default.

> **Breakpoint, line 118:** Inspect `query` to see the exact SQL the model wrote.

### run_sql_query(), part 2: read-only execution (safety layer 3) (lines 122–136)

```python
122      if not Path(settings.db_path).exists():
123          return {"error": f"No database found at {settings.db_path}. Run: python -m bi_agent.cli load-data"}
124  
125      uri = f"file:{Path(settings.db_path).as_posix()}?mode=ro"
126      try:
127          conn = sqlite3.connect(uri, uri=True)
128          conn.row_factory = sqlite3.Row
129          conn.execute("PRAGMA query_only = ON")
130          cursor = conn.execute(body)
131          columns = [d[0] for d in cursor.description] if cursor.description else []
132          rows = cursor.fetchmany(cap + 1)
133      except sqlite3.Error as exc:
134          return {"error": f"SQL error: {exc}"}
135      finally:
136          conn.close()
```

- Line 125: `?mode=ro` opens the file **read-only** at the SQLite level. Even if a dangerous query slipped past the checks, the database would refuse to write.
- Line 129: `PRAGMA query_only` is a second read-only switch on the same connection.
- Line 132: `fetchmany(cap + 1)` reads at most 501 rows. Fetching one extra is a cheap way to know whether there were more than 500.
- Lines 133-134: a SQL error (bad column name, syntax error) becomes an error dict.

> **Breakpoint, line 130:** Step over this line to watch SQLite execute the model's query. A wrong column name raises here.

### run_sql_query(), part 3: the row cap (safety layer 4) (lines 138–146)

```python
138      truncated = len(rows) > cap
139      rows = rows[:cap]
140      return {
141          "sql": body,
142          "columns": columns,
143          "rows": [dict(r) for r in rows],
144          "row_count": len(rows),
145          "truncated": truncated,
146      }
```

If 501 rows came back, `truncated` is True and only the first 500 are kept. `dict(r)` converts each `sqlite3.Row` into a plain dict so the result can be turned into JSON.


## `bi_agent/charts.py`

Turns query rows into a styled PNG chart.

### Docstring and imports (lines 1–22)

```python
 1  """
 2  create_chart(): turn a query result (list of dicts) into a saved PNG.
 3  
 4  Color and form choices here follow a validated design-system palette
 5  (categorical hues ordered and checked for colorblind-safe adjacent
 6  contrast; sequential = one hue, light "near zero" -> dark "high"; never a
 7  dual axis; never more than ~8 categorical series without folding the tail
 8  into "Other") rather than whatever matplotlib defaults to. See the
 9  docstring on each constant for where its value comes from.
10  """
11  from __future__ import annotations
12  
13  from pathlib import Path
14  from typing import Any
15  
16  import matplotlib
17  
18  matplotlib.use("Agg")  # headless -- this runs as a backend tool, not a notebook
19  import matplotlib.pyplot as plt
20  from matplotlib.ticker import FuncFormatter
21  
22  from .config import Settings, get_settings
```

Line 18, `matplotlib.use("Agg")`, selects the non-interactive backend: draw into memory and save to a file, never open a window. It must come before `pyplot` is imported. Without it, charting can crash inside Streamlit, which runs in a background thread with no display.

### Palette and limits (lines 24–39)

```python
24  # -- Palette (validated: adjacent-pair colorblind-safe in fixed order; do
25  #    not reorder or cycle past 8 without re-validating) --------------------
26  CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
27  SEQUENTIAL_BLUE = "#2a78d6"  # single-hue default for magnitude-only (one series) charts
28  SURFACE = "#fcfcfb"
29  INK_PRIMARY = "#0b0b0b"
30  INK_SECONDARY = "#52514e"
31  INK_MUTED = "#898781"
32  GRIDLINE = "#e1e0d9"
33  BASELINE = "#c3c2b7"
34  
35  MAX_SERIES = 8  # beyond this, fold the tail into "Other" rather than generate a 9th hue
36  
37  
38  class ChartError(ValueError):
39      pass
```

Fixed colors instead of matplotlib's defaults. `CATEGORICAL` is used in this exact order for multi-series charts; `SEQUENTIAL_BLUE` for single-series ones. `MAX_SERIES = 8` caps how many colored lines or bars one chart can have.

### _slugify() and _style_axes() (lines 42–60)

```python
42  def _slugify(text: str) -> str:
43      keep = [c.lower() if c.isalnum() else "_" for c in text]
44      slug = "".join(keep).strip("_")
45      while "__" in slug:
46          slug = slug.replace("__", "_")
47      return slug or "chart"
48  
49  
50  def _style_axes(ax, horizontal_grid: bool = True) -> None:
51      ax.set_facecolor(SURFACE)
52      for side in ("top", "right"):
53          ax.spines[side].set_visible(False)
54      for side in ("left", "bottom"):
55          ax.spines[side].set_color(BASELINE)
56      ax.tick_params(colors=INK_MUTED, labelsize=9)
57      if horizontal_grid:
58          ax.yaxis.grid(True, color=GRIDLINE, linewidth=1, zorder=0)
59          ax.xaxis.grid(False)
60      ax.set_axisbelow(True)
```

`_slugify("Top 5 categories!")` gives `top_5_categories`, used as the PNG file name.

`_style_axes` removes the top and right borders, mutes the tick labels and draws light gridlines behind the data (`set_axisbelow`).

### Axis helpers (lines 63–81)

```python
63  def _compact_number(v: float, _pos=None) -> str:
64      """Axis tick text: 1,441,248 -> '1.4M', 902,512 -> '903K', 12.5 -> '12.5'.
65      Replaces matplotlib's default offset notation (a lone '1e6' in the corner)."""
66      a = abs(v)
67      for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
68          if a >= div:
69              out = f"{v / div:.1f}".rstrip("0").rstrip(".")
70              return f"{out}{suffix}"
71      return f"{v:g}"
72  
73  
74  def _thin_category_ticks(ax, n: int, max_labels: int = 12) -> None:
75      """Long ordered axes (e.g. 24 months) get every k-th label so they don't collide."""
76      if n <= max_labels:
77          return
78      step = -(-n // max_labels)  # ceil
79      for i, label in enumerate(ax.get_xticklabels()):
80          label.set_visible(i % step == 0)
81  
```

- `_compact_number` turns `1441248` into `1.4M` for axis labels. `FuncFormatter` (line 185) calls it for every tick.
- `_thin_category_ticks` hides some x labels when there are more than 12 (e.g. 24 months), so they don't overlap. `-(-n // 12)` is a ceiling division trick.

### _fold_series() (lines 83–93)

```python
83  def _fold_series(rows: list[dict], series_field: str, y_field: str) -> list[str]:
84      """Return the series values to actually plot, folding anything past
85      MAX_SERIES-1 into an 'Other' bucket (by total magnitude, ascending)."""
86      totals: dict[str, float] = {}
87      for r in rows:
88          key = str(r.get(series_field))
89          totals[key] = totals.get(key, 0.0) + float(r.get(y_field) or 0)
90      ordered = sorted(totals, key=lambda k: totals[k], reverse=True)
91      if len(ordered) <= MAX_SERIES:
92          return ordered
93      return ordered[: MAX_SERIES - 1] + ["Other"]
```

When a chart has a `series` column (e.g. one line per state), this totals each series, keeps the 7 largest and folds the rest into "Other", so there are never more than 8 colors.

### create_chart(): validate, draw, save (lines 96–145)

```python
 96  def create_chart(
 97      data: list[dict[str, Any]],
 98      chart_type: str,
 99      x: str,
100      y: str,
101      series: str | None = None,
102      title: str = "",
103      x_label: str | None = None,
104      y_label: str | None = None,
105      out_name: str | None = None,
106      settings: Settings | None = None,
107  ) -> dict[str, Any]:
108      """
109      Args:
110          data: rows from run_sql_query()['rows'] (or any list of dicts).
111          chart_type: "bar" | "hbar" | "line" | "stacked_bar".
112          x: field name for the category/time axis.
113          y: field name for the numeric value.
114          series: optional field name to split into multiple series
115              (grouped for bar/line, stacked for stacked_bar). Capped at 8
116              distinct values; the rest fold into "Other".
117          title, x_label, y_label: chart text. x_label/y_label default to x/y.
118          out_name: filename (without extension) under charts_dir. Defaults
119              to a slug of the title.
120  
121      Returns a dict with 'chart_path' on success, or 'error' on failure --
122      safe to call directly from an LLM tool-calling loop.
123      """
124      settings = settings or get_settings()
125      if not data:
126          return {"error": "No data to chart (empty result set)."}
127      if chart_type not in ("bar", "hbar", "line", "stacked_bar"):
128          return {"error": f"Unknown chart_type '{chart_type}'. Use bar, hbar, line, or stacked_bar."}
129      if x not in data[0] or y not in data[0]:
130          return {"error": f"Fields '{x}' and/or '{y}' not found in the data's columns: {list(data[0].keys())}"}
131  
132      try:
133          settings.charts_dir.mkdir(parents=True, exist_ok=True)
134          if series:
135              fig = _render_multi_series(data, chart_type, x, y, series, title, x_label, y_label)
136          else:
137              fig = _render_single_series(data, chart_type, x, y, title, x_label, y_label)
138      except Exception as exc:  # noqa: BLE001 -- surface as a tool error, not a crash
139          return {"error": f"Failed to render chart: {exc}"}
140  
141      name = out_name or _slugify(title or f"{chart_type}_{x}_{y}")
142      path = settings.charts_dir / f"{name}.png"
143      fig.savefig(path, dpi=150, facecolor=SURFACE, bbox_inches="tight")
144      plt.close(fig)
145      return {"chart_path": str(path), "chart_type": chart_type}
```

- Lines 125-130 check the inputs and return error dicts the model can act on. The column check (line 129) catches the common mistake of the model naming a column its SQL didn't actually return.
- Lines 134-137 pick the single- or multi-series renderer.
- Lines 141-144 save the PNG and close the figure. `plt.close(fig)` matters in a long-running app: open figures keep using memory.

Returns `{"chart_path": ...}`, the file the agent loop then reads into memory.

> **Breakpoint, line 129:** Inspect `data[0].keys()` against `x` and `y` when a chart fails.

### _render_single_series() (lines 148–196)

```python
148  def _render_single_series(data, chart_type, x, y, title, x_label, y_label):
149      xs = [r[x] for r in data]
150      ys = [float(r[y]) if r[y] is not None else 0.0 for r in data]
151      label_values = len(xs) <= 10
152  
153      if chart_type == "hbar":
154          height = max(3, 0.4 * len(xs))
155          fig, ax = plt.subplots(figsize=(8, height))
156          bars = ax.barh(xs, ys, color=SEQUENTIAL_BLUE)
157          ax.invert_yaxis()
158          _style_axes(ax, horizontal_grid=False)
159          ax.xaxis.grid(True, color=GRIDLINE, linewidth=1, zorder=0)
160          if label_values:
161              for bar, v in zip(bars, ys):
162                  ax.text(
163                      bar.get_width(), bar.get_y() + bar.get_height() / 2,
164                      f" {v:,.0f}", va="center", ha="left", color=INK_SECONDARY, fontsize=8,
165                  )
166      elif chart_type == "line":
167          fig, ax = plt.subplots(figsize=(8, 5))
168          ax.plot(xs, ys, color=SEQUENTIAL_BLUE, linewidth=2, marker="o", markersize=4)
169          _style_axes(ax)
170          plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
171          _thin_category_ticks(ax, len(xs))
172      else:  # "bar" and "stacked_bar" with no series collapse to a plain bar
173          fig, ax = plt.subplots(figsize=(8, 5))
174          bars = ax.bar(xs, ys, color=SEQUENTIAL_BLUE)
175          _style_axes(ax)
176          plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
177          if label_values:
178              for bar, v in zip(bars, ys):
179                  ax.text(
180                      bar.get_x() + bar.get_width() / 2, bar.get_height(),
181                      f"{v:,.0f}", va="bottom", ha="center", color=INK_SECONDARY, fontsize=8,
182                  )
183  
184      value_axis = ax.xaxis if chart_type == "hbar" else ax.yaxis
185      value_axis.set_major_formatter(FuncFormatter(_compact_number))
186      ax.set_title(title, color=INK_PRIMARY, fontsize=13, pad=12, loc="left")
187      if chart_type == "hbar":
188          # barh puts categories on the y-axis and the numeric field on the x-axis --
189          # the opposite of every other chart_type here, so the label assignment flips too.
190          ax.set_xlabel(y_label or y, color=INK_MUTED, fontsize=9)
191          ax.set_ylabel(x_label or x, color=INK_MUTED, fontsize=9)
192      else:
193          ax.set_xlabel(x_label or x, color=INK_MUTED, fontsize=9)
194          ax.set_ylabel(y_label or y, color=INK_MUTED, fontsize=9)
195      fig.tight_layout()
196      return fig
```

One `if/elif/else` per chart shape:

- `hbar`: horizontal bars, best for long category names. `invert_yaxis()` puts the biggest bar on top. Bars get value labels when there are 10 or fewer.
- `line`: for time trends. Labels rotate 30° and get thinned.
- otherwise: vertical bars.

Lines 184-194: on a horizontal chart the numeric axis is x, not y, so the number formatter and the axis titles swap sides.

### _render_multi_series() (lines 199–248)

```python
199  def _render_multi_series(data, chart_type, x, y, series, title, x_label, y_label):
200      series_values = _fold_series(data, series, y)
201      categories = sorted({r[x] for r in data}, key=lambda v: (v is None, v))
202  
203      # pivot: {series_value: {category: total_y}}
204      pivot: dict[str, dict[Any, float]] = {sv: {c: 0.0 for c in categories} for sv in series_values}
205      kept = set(series_values) - {"Other"}
206      for r in data:
207          key = str(r.get(series))
208          bucket = key if key in kept else "Other"
209          if bucket not in pivot:
210              continue
211          pivot[bucket][r[x]] = pivot[bucket].get(r[x], 0.0) + float(r.get(y) or 0)
212  
213      colors = dict(zip(series_values, CATEGORICAL))
214  
215      fig, ax = plt.subplots(figsize=(max(8, 0.6 * len(categories)), 5))
216  
217      if chart_type == "line":
218          for sv in series_values:
219              ys = [pivot[sv][c] for c in categories]
220              ax.plot(categories, ys, label=sv, color=colors[sv], linewidth=2, marker="o", markersize=4)
221      elif chart_type == "stacked_bar":
222          bottoms = [0.0] * len(categories)
223          for sv in series_values:
224              ys = [pivot[sv][c] for c in categories]
225              ax.bar(categories, ys, bottom=bottoms, label=sv, color=colors[sv],
226                     edgecolor=SURFACE, linewidth=1)
227              bottoms = [b + v for b, v in zip(bottoms, ys)]
228      else:  # grouped "bar" or "hbar" collapse to grouped vertical bars
229          n = len(series_values)
230          width = 0.8 / max(n, 1)
231          offsets = [(-0.4 + width / 2) + i * width for i in range(n)]
232          x_idx = range(len(categories))
233          for sv, off in zip(series_values, offsets):
234              ys = [pivot[sv][c] for c in categories]
235              ax.bar([i + off for i in x_idx], ys, width=width, label=sv, color=colors[sv])
236          ax.set_xticks(list(x_idx))
237          ax.set_xticklabels(categories)
238  
239      _style_axes(ax)
240      plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
241      _thin_category_ticks(ax, len(categories))
242      ax.yaxis.set_major_formatter(FuncFormatter(_compact_number))
243      ax.set_title(title, color=INK_PRIMARY, fontsize=13, pad=12, loc="left")
244      ax.set_xlabel(x_label or x, color=INK_MUTED, fontsize=9)
245      ax.set_ylabel(y_label or y, color=INK_MUTED, fontsize=9)
246      legend = ax.legend(frameon=False, fontsize=9, labelcolor=INK_SECONDARY)
247      fig.tight_layout()
248      return fig
```

- Line 200: decide which series to draw.
- Lines 203-211: pivot the rows into `{series: {category: total}}`. Series not kept are summed into "Other".
- Lines 217-237: draw lines, stacked bars (each series starts at the top of the previous one via `bottom=`), or grouped bars (each series shifted sideways by an offset).
- Line 246: a legend, which is only needed when there's more than one series.


## `bi_agent/sentiment.py`

Scores the Portuguese review comments as positive, neutral or negative.

### Docstring and imports (lines 1–31)

```python
 1  """
 2  Review sentiment scoring -- the NLP/transformers piece of this project.
 3  
 4  order_reviews.review_comment_message is free-text Portuguese written by real
 5  customers, which makes it a legitimate target for a transformer sentiment
 6  model rather than a toy example. Two backends share one interface:
 7  
 8    - TransformerSentimentAnalyzer: a real multilingual transformer
 9      (nlptown/bert-base-multilingual-uncased-sentiment, a BERT fine-tuned for
10      1-5 star sentiment across several languages including Portuguese) via
11      the `transformers` pipeline API. This is the one you'd actually cite on
12      a resume.
13    - LexiconSentimentAnalyzer: a small, dependency-free Portuguese
14      keyword-matching fallback. It exists so the rest of the system (the
15      DB table, the batch scorer, the agent tool, the tests) keeps working
16      in an environment with no internet access or without `transformers`/
17      `torch` installed -- which is exactly the situation this was built and
18      tested in. It is clearly a worse model and `score_reviews()` always
19      records which backend produced a given row (review_sentiment.backend),
20      so nothing is silently misrepresented as transformer output.
21  
22  get_sentiment_analyzer("auto") tries the transformer first and falls back
23  to the lexicon with a printed warning -- "auto" is the default everywhere
24  in this project so the same code behaves well on a fully-equipped machine
25  and a locked-down one alike.
26  """
27  from __future__ import annotations
28  
29  import sqlite3
30  from dataclasses import dataclass
31  from typing import Protocol
```

Two interchangeable scorers behind one interface: a real transformer model and a keyword fallback.

### Shared types (lines 33–51)

```python
33  # -- Shared result type / interface ----------------------------------------
34  
35  
36  @dataclass(frozen=True)
37  class SentimentResult:
38      label: str  # "positive" | "neutral" | "negative"
39      score: float  # 0..1, confidence/strength
40      backend: str  # "transformer" | "lexicon"
41  
42  
43  class SentimentAnalyzer(Protocol):
44      backend_name: str
45  
46      def analyze(self, text: str) -> SentimentResult: ...
47  
48  
49  class SentimentBackendUnavailable(RuntimeError):
50      """Raised when a requested backend can't be constructed (e.g. no internet
51      to download transformer weights, or the optional packages aren't installed)."""
```

- `SentimentResult`: what every scorer returns (label, confidence, which backend).
- `SentimentAnalyzer(Protocol)`: the interface. Any class with a `backend_name` and an `analyze(text)` method qualifies, with no inheritance needed. That's how the two scorers can be swapped freely.
- `SentimentBackendUnavailable`: raised when the transformer can't load.

### TransformerSentimentAnalyzer: loading (lines 54–82)

```python
54  # -- Transformer backend -----------------------------------------------------
55  
56  _TRANSFORMER_MODEL = "nlptown/bert-base-multilingual-uncased-sentiment"
57  
58  
59  class TransformerSentimentAnalyzer:
60      """Wraps a HF `transformers` sentiment pipeline. Loading happens in
61      __init__ (not lazily on first call) so failures surface immediately and
62      get_sentiment_analyzer('auto') can fall back cleanly instead of failing
63      midway through a batch."""
64  
65      backend_name = "transformer"
66  
67      def __init__(self, model_name: str = _TRANSFORMER_MODEL) -> None:
68          try:
69              from transformers import pipeline  # type: ignore
70          except ImportError as exc:
71              raise SentimentBackendUnavailable(
72                  "The 'transformers'/'torch' packages aren't installed. "
73                  "Run: pip install -r requirements-nlp.txt"
74              ) from exc
75          try:
76              self._pipeline = pipeline("sentiment-analysis", model=model_name)
77          except Exception as exc:  # network error, model-not-cached, etc.
78              raise SentimentBackendUnavailable(
79                  f"Could not load transformer model '{model_name}' "
80                  f"(likely no internet access to huggingface.co on first use, "
81                  f"or the weights aren't cached locally yet): {exc}"
82              ) from exc
```

- Lines 68-74: import `transformers` inside the method, so the project still runs when it isn't installed.
- Line 76: `pipeline("sentiment-analysis", model=...)` downloads the BERT model from Hugging Face the first time (about 650 MB, then cached) and loads it.

Loading in `__init__` means a failure shows up immediately, before any review is scored.

### TransformerSentimentAnalyzer: scoring one review (lines 84–94)

```python
84      def analyze(self, text: str) -> SentimentResult:
85          # nlptown's model returns labels like "1 star" .. "5 stars".
86          result = self._pipeline(text[:512])[0]  # truncate -- reviews are short anyway
87          stars = int(result["label"][0])
88          if stars >= 4:
89              label = "positive"
90          elif stars == 3:
91              label = "neutral"
92          else:
93              label = "negative"
94          return SentimentResult(label=label, score=float(result["score"]), backend=self.backend_name)
```

The model returns something like `{"label": "4 stars", "score": 0.62}`. Line 87 takes the first character as the star count; lines 88-93 map 4-5 to positive, 3 to neutral, 1-2 to negative. `text[:512]` trims very long reviews (BERT's input is limited).

> **Breakpoint, line 87:** Inspect `result` to see the model's raw star prediction and confidence.

### LexiconSentimentAnalyzer (lines 97–132)

```python
 97  # -- Lexicon fallback ---------------------------------------------------------
 98  
 99  _POSITIVE_WORDS = {
100      "bom", "boa", "otimo", "ótimo", "otima", "ótima", "excelente", "adorei", "amei",
101      "recomendo", "perfeito", "perfeita", "rapido", "rápido", "rapida", "rápida",
102      "show", "maravilhoso", "maravilhosa", "gostei", "satisfeito", "satisfeita",
103      "qualidade", "sucesso", "feliz", "incrivel", "incrível", "top", "eficiente",
104  }
105  _NEGATIVE_WORDS = {
106      "ruim", "pessimo", "péssimo", "pessima", "péssima", "horrivel", "horrível",
107      "nao recomendo", "não recomendo", "demorou", "quebrado", "quebrada", "defeito",
108      "errado", "errada", "atrasado", "atrasada", "cancelado", "cancelada", "problema",
109      "insatisfeito", "insatisfeita", "enganacao", "enganação", "veio errado",
110      "nao chegou", "não chegou", "nao gostei", "não gostei", "lixo", "decepcionado",
111      "decepcionada",
112  }
113  
114  
115  class LexiconSentimentAnalyzer:
116      """Deterministic, dependency-free keyword scorer. Deliberately simple --
117      this is the safety-net backend, not the one doing the real work."""
118  
119      backend_name = "lexicon"
120  
121      def analyze(self, text: str) -> SentimentResult:
122          normalized = (text or "").lower()
123          pos = sum(1 for w in _POSITIVE_WORDS if w in normalized)
124          neg = sum(1 for w in _NEGATIVE_WORDS if w in normalized)
125          total = pos + neg
126          if total == 0:
127              return SentimentResult(label="neutral", score=0.5, backend=self.backend_name)
128          if pos > neg:
129              return SentimentResult(label="positive", score=pos / total, backend=self.backend_name)
130          if neg > pos:
131              return SentimentResult(label="negative", score=neg / total, backend=self.backend_name)
132          return SentimentResult(label="neutral", score=0.5, backend=self.backend_name)
```

Counts how many positive and negative Portuguese words appear in the lowercased text and returns whichever side has more. Ties and no matches are neutral. The confidence is the winning side's share of matches.

This is a fallback, and it has real weaknesses (see "Things you'll notice while debugging").

### get_sentiment_analyzer(): pick a backend (lines 135–150)

```python
135  # -- Factory -------------------------------------------------------------
136  
137  
138  def get_sentiment_analyzer(backend: str = "auto") -> SentimentAnalyzer:
139      if backend == "lexicon":
140          return LexiconSentimentAnalyzer()
141      if backend == "transformer":
142          return TransformerSentimentAnalyzer()  # let it raise -- caller asked explicitly
143      if backend != "auto":
144          raise ValueError(f"Unknown sentiment backend '{backend}'. Use auto, transformer, or lexicon.")
145  
146      try:
147          return TransformerSentimentAnalyzer()
148      except SentimentBackendUnavailable as exc:
149          print(f"[bi_agent.sentiment] falling back to lexicon backend: {exc}")
150          return LexiconSentimentAnalyzer()
```

`"auto"` (the default) tries the transformer and falls back to the lexicon on failure, printing why. Asking for `"transformer"` explicitly lets the error through instead, so you find out it's missing.

> **Breakpoint, line 147:** Step into this line to see whether the transformer loads on your machine, and why not if it doesn't.

### score_reviews(): batch scoring into the database (lines 153–195)

```python
153  # -- Batch scoring over the DB -----------------------------------------------
154  
155  
156  def score_reviews(
157      conn: sqlite3.Connection,
158      analyzer: SentimentAnalyzer,
159      limit: int = 200,
160      rescore: bool = False,
161  ) -> dict[str, int]:
162      """Score up to `limit` reviews that have comment text, writing results
163      into review_sentiment (upsert by review_id). By default skips reviews
164      already scored, so re-running is cheap; rescore=True re-scores everyone
165      (e.g. after switching backends).
166      """
167      where_scored = "" if rescore else "AND r.review_id NOT IN (SELECT review_id FROM review_sentiment)"
168      rows = conn.execute(
169          f"""
170          SELECT r.review_id, r.review_comment_message
171          FROM order_reviews r
172          WHERE r.review_comment_message IS NOT NULL
173            AND TRIM(r.review_comment_message) != ''
174            {where_scored}
175          LIMIT ?
176          """,
177          (limit,),
178      ).fetchall()
179  
180      counts = {"scored": 0, "positive": 0, "neutral": 0, "negative": 0}
181      for row in rows:
182          result = analyzer.analyze(row["review_comment_message"])
183          conn.execute(
184              """
185              INSERT INTO review_sentiment (review_id, label, score, backend)
186              VALUES (?, ?, ?, ?)
187              ON CONFLICT(review_id) DO UPDATE SET
188                  label=excluded.label, score=excluded.score, backend=excluded.backend
189              """,
190              (row["review_id"], result.label, result.score, result.backend),
191          )
192          counts["scored"] += 1
193          counts[result.label] += 1
194      conn.commit()
195      return counts
```

- Line 167: unless `rescore=True`, skip reviews already in `review_sentiment`. That's why each "Score 1,000 more" click scores new reviews.
- Lines 168-178: fetch up to `limit` reviews that have comment text.
- Lines 181-193: score each and **upsert** it: `ON CONFLICT(review_id) DO UPDATE` inserts a new row, or replaces the existing one.
- Line 194: one commit at the end.


## `bi_agent/tools.py`

The four tools the model can call: Python functions plus the JSON descriptions the model reads.

### Docstring and imports (lines 1–25)

```python
 1  """
 2  Agent-facing tools: plain Python functions + Anthropic-format tool schemas,
 3  same pattern as football_agent.tools. TOOLS goes straight into a
 4  `tools=` kwarg; TOOL_FUNCTIONS maps name -> callable for a dispatch loop.
 5  
 6  Four tools, each doing one job:
 7      get_schema              -- "what can I even query?"
 8      run_sql_query            -- read-only SQL -> rows (guarded, see query.py)
 9      create_chart_from_query  -- read-only SQL -> rendered PNG (guarded + charted)
10      get_review_sentiment_summary -- pre-scored NLP sentiment, aggregated
11  
12  create_chart_from_query takes a SQL string rather than inline row data on
13  purpose: an LLM re-typing a large JSON result back into its own next tool
14  call is slow, expensive, and error-prone. Handing it a SQL string instead
15  means the chart tool re-runs the (cheap, already-validated) query itself.
16  """
17  from __future__ import annotations
18  
19  from typing import Any
20  
21  from . import db
22  from .charts import create_chart as _create_chart
23  from .config import get_settings
24  from .query import get_schema as _get_schema
25  from .query import run_sql_query as _run_sql_query
```

The core idea of the file: each tool exists twice. Once as a Python function (what actually runs), and once as a JSON description in `TOOLS` (what the model sees). `TOOL_FUNCTIONS` at the bottom connects the two by name.

### get_schema and run_sql_query (lines 28–42)

```python
28  def get_schema() -> dict[str, Any]:
29      """List every table/view available to query, with columns and a short description of each."""
30      return _get_schema()
31  
32  
33  def run_sql_query(query: str) -> dict[str, Any]:
34      """Run a read-only SQL SELECT against the BI database and return the rows.
35  
36      Args:
37          query: A single SELECT (or WITH ... SELECT) statement. No writes,
38              no DDL, no multiple statements. Results are capped at the
39              server's configured row limit (truncated=true if you hit it --
40              add your own LIMIT/aggregation to narrow it down).
41      """
42      return _run_sql_query(query)
```

Thin wrappers around `query.py`. The parameter names here (`query`) must exactly match the names in the JSON schema below, because the agent calls them as `fn(**tool_input)`.

### create_chart_from_query (lines 45–78)

```python
45  def create_chart_from_query(
46      query: str,
47      chart_type: str,
48      x: str,
49      y: str,
50      series: str | None = None,
51      title: str = "",
52      x_label: str | None = None,
53      y_label: str | None = None,
54  ) -> dict[str, Any]:
55      """Run a read-only SQL query and render the results as a chart PNG.
56  
57      Args:
58          query: SELECT statement whose result columns include `x`, `y`, and
59              (if given) `series`. Aggregate/GROUP BY in the SQL itself --
60              this does not re-aggregate.
61          chart_type: "bar" (vertical columns), "hbar" (horizontal bars --
62              better for many or long category names), "line" (trend over
63              time/ordered axis), or "stacked_bar" (part-to-whole across
64              `series`).
65          x: result column to use for the category/time axis.
66          y: result column to use for the numeric value.
67          series: optional result column to split into multiple series
68              (grouped for bar/line, stacked for stacked_bar). Capped at 8
69              distinct values; extras fold into "Other".
70          title, x_label, y_label: chart text.
71      """
72      result = _run_sql_query(query)
73      if "error" in result:
74          return result
75      return _create_chart(
76          data=result["rows"], chart_type=chart_type, x=x, y=y, series=series,
77          title=title, x_label=x_label, y_label=y_label,
78      )
```

Runs the SQL itself (line 72), stops if that failed (lines 73-74), then hands the rows to `charts.create_chart`.

Taking SQL instead of data means the model never has to copy hundreds of rows into its next request.

> **Breakpoint, line 72:** Compare the model's SQL with the rows it produced before the chart is drawn.

### get_review_sentiment_summary (lines 81–152)

```python
 81  def get_review_sentiment_summary(
 82      category: str | None = None,
 83      state: str | None = None,
 84  ) -> dict[str, Any]:
 85      """Summarize customer review sentiment (already scored by the NLP
 86      pipeline -- see bi_agent.sentiment / `cli.py score-reviews`), optionally
 87      filtered to a product category or customer state.
 88  
 89      Args:
 90          category: English product category (e.g. "health_beauty"). Optional.
 91          state: 2-letter Brazilian customer state code (e.g. "SP"). Optional.
 92      """
 93      settings = get_settings()
 94      conn = db.connect(settings.db_path)
 95      try:
 96          filters = []
 97          params: list[Any] = []
 98          if category:
 99              filters.append("v.category_english = ?")
100              params.append(category)
101          if state:
102              filters.append("v.customer_state = ?")
103              params.append(state)
104          where = f"WHERE {' AND '.join(filters)}" if filters else ""
105  
106          scored = conn.execute("SELECT COUNT(*) AS c FROM review_sentiment").fetchone()["c"]
107          if scored == 0:
108              return {
109                  "error": "No reviews have been sentiment-scored yet. Run: "
110                           "python -m bi_agent.cli score-reviews"
111              }
112  
113          counts = conn.execute(
114              f"""
115              SELECT rs.label, COUNT(DISTINCT rs.review_id) AS n
116              FROM review_sentiment rs
117              JOIN order_reviews r ON r.review_id = rs.review_id
118              JOIN v_order_items_full v ON v.order_id = r.order_id
119              {where}
120              GROUP BY rs.label
121              """,
122              params,
123          ).fetchall()
124          count_map = {"positive": 0, "neutral": 0, "negative": 0}
125          for row in counts:
126              count_map[row["label"]] = row["n"]
127          total = sum(count_map.values())
128  
129          examples = conn.execute(
130              f"""
131              SELECT DISTINCT rs.label, r.review_comment_message
132              FROM review_sentiment rs
133              JOIN order_reviews r ON r.review_id = rs.review_id
134              JOIN v_order_items_full v ON v.order_id = r.order_id
135              {where}
136              ORDER BY rs.score DESC
137              LIMIT 6
138              """,
139              params,
140          ).fetchall()
141  
142          return {
143              "filters": {"category": category, "state": state},
144              "reviews_scored_in_scope": total,
145              "counts": count_map,
146              "example_comments": [
147                  {"label": r["label"], "comment": r["review_comment_message"][:200]}
148                  for r in examples
149              ],
150          }
151      finally:
152          conn.close()
```

- Lines 96-104 build an optional `WHERE` clause. The values go in as `?` **parameters**, never pasted into the SQL string, which prevents SQL injection.
- Lines 106-111: if nothing has been scored yet, say so in a way the model can relay ("run score-reviews").
- Lines 113-127: count reviews per label, joining sentiment → review → order items so it can filter by category or state. `COUNT(DISTINCT ...)` matters because one review can match several items of the same order.
- Lines 129-140: pick up to 6 example comments, trimmed to 200 characters on line 147.

> **Breakpoint, line 113:** Inspect `where` and `params` to see how a category or state filter was built.

### TOOLS: what the model sees (lines 155–202)

```python
155  TOOLS: list[dict[str, Any]] = [
156      {
157          "name": "get_schema",
158          "description": "List every table/view available to query in the BI database, with columns and a short description of each. Call this before writing SQL if you haven't already seen the schema.",
159          "input_schema": {"type": "object", "properties": {}, "required": []},
160      },
161      {
162          "name": "run_sql_query",
163          "description": "Run a read-only SQL SELECT against the e-commerce BI database and get back the rows. Use v_order_items_full for most revenue/order questions -- it's already a wide, pre-joined view.",
164          "input_schema": {
165              "type": "object",
166              "properties": {
167                  "query": {"type": "string", "description": "A single SELECT (or WITH ... SELECT) statement."},
168              },
169              "required": ["query"],
170          },
171      },
172      {
173          "name": "create_chart_from_query",
174          "description": "Run a read-only SQL query and render the result as a chart image (PNG). Use this whenever the user wants to 'see', 'visualize', 'chart', or 'plot' something, instead of just describing numbers in text.",
175          "input_schema": {
176              "type": "object",
177              "properties": {
178                  "query": {"type": "string", "description": "SELECT statement whose result columns include x, y, and (if given) series."},
179                  "chart_type": {"type": "string", "enum": ["bar", "hbar", "line", "stacked_bar"]},
180                  "x": {"type": "string", "description": "Result column for the category/time axis."},
181                  "y": {"type": "string", "description": "Result column for the numeric value."},
182                  "series": {"type": "string", "description": "Optional result column to split into multiple series."},
183                  "title": {"type": "string"},
184                  "x_label": {"type": "string"},
185                  "y_label": {"type": "string"},
186              },
187              "required": ["query", "chart_type", "x", "y"],
188          },
189      },
190      {
191          "name": "get_review_sentiment_summary",
192          "description": "Get a summary of customer review sentiment (positive/neutral/negative counts + example comments), computed by a transformer NLP model over review text. Optionally filter by product category or customer state.",
193          "input_schema": {
194              "type": "object",
195              "properties": {
196                  "category": {"type": "string", "description": "English product category, e.g. 'health_beauty'."},
197                  "state": {"type": "string", "description": "2-letter Brazilian state code, e.g. 'SP'."},
198              },
199              "required": [],
200          },
201      },
202  ]
```

This list is sent to Claude with every request. Each entry has:

- `name`: what the model writes when it wants this tool.
- `description`: the model reads this to decide **when** to use the tool. "Use this whenever the user wants to see, visualize, chart..." is why it reaches for charts.
- `input_schema`: a JSON Schema of the arguments. `enum` on line 179 restricts `chart_type` to four values; `required` says which arguments are mandatory.

Editing these descriptions is the main way to change the agent's behavior without changing any logic.

### TOOL_FUNCTIONS (lines 204–209)

```python
204  TOOL_FUNCTIONS = {
205      "get_schema": get_schema,
206      "run_sql_query": run_sql_query,
207      "create_chart_from_query": create_chart_from_query,
208      "get_review_sentiment_summary": get_review_sentiment_summary,
209  }
```

Maps each tool name to its Python function. `agent.py` looks up `TOOL_FUNCTIONS[name]` when the model asks for a tool.


## `bi_agent/agent.py`

The agent loop: talks to Claude, runs the tools it asks for, repeats until there's an answer.

### Docstring and imports (lines 1–33)

```python
 1  """
 2  The agent loop, as a reusable function instead of a script.
 3  
 4  `run_agent()` is the same tool-calling loop agent_demo.py always had --
 5  model sees TOOLS, picks one, TOOL_FUNCTIONS[name](**input) runs it, result
 6  goes back, repeat until the model answers in plain text -- but packaged so
 7  more than one front end can drive it:
 8  
 9      agent_demo.py   -> prints tool calls to stderr, final answer to stdout
10      app.py          -> Streamlit chat UI that shows each tool call live
11                         (the SQL, the rows, the chart) as it happens
12  
13  Two things it adds over the original script loop:
14  
15    * Multi-turn memory: it takes and returns the full message history, so a
16      follow-up like "now split that by state" works.
17    * Guard rails on the loop itself: a tool that raises becomes an error
18      result the model can read (not a crash), an unknown tool name is
19      reported back the same way, and `max_steps` stops a model that keeps
20      calling tools forever.
21  
22  The Anthropic client is injected (`client=`), so tests can drive the whole
23  loop with a fake client and no network or API key.
24  """
25  from __future__ import annotations
26  
27  import json
28  import os
29  from dataclasses import dataclass, field
30  from pathlib import Path
31  from typing import Any, Callable
32  
33  from .tools import TOOL_FUNCTIONS, TOOLS
```

The heart of the project. Both front ends (`app.py` and `agent_demo.py`) call `run_agent()`.

### Model and system prompt (lines 35–48)

```python
35  DEFAULT_MODEL = os.getenv("BI_AGENT_MODEL", "claude-haiku-4-5-20251001")
36  DEFAULT_MAX_STEPS = 10
37  
38  SYSTEM_PROMPT = (
39      "You are a BI analyst assistant for an e-commerce marketplace (Olist, Brazil, 2016-2018). "
40      "You have tools to inspect the database schema, run read-only SQL, render charts, "
41      "and read customer review sentiment. Always check the schema before writing SQL if "
42      "you haven't already in this conversation. Prefer v_order_items_full for order/revenue "
43      "questions. When the user asks to see, chart, or visualize something, use "
44      "create_chart_from_query instead of just describing numbers. If a query errors, read the "
45      "error, fix the SQL, and try again. Finish with a short, stakeholder-readable summary of "
46      "what you found -- key numbers and what they mean, not a dump of raw rows. Currency is "
47      "Brazilian reais (R$)."
48  )
```

- `DEFAULT_MODEL`: Claude Haiku 4.5 unless `BI_AGENT_MODEL` is set.
- `DEFAULT_MAX_STEPS = 10`: at most 10 rounds of tool calls per question.
- `SYSTEM_PROMPT`: standing instructions sent with every request. It tells the model to check the schema first, prefer the view, chart when asked, retry after errors, and finish with a short business summary in reais. Editing this text is the quickest way to change how the agent behaves.

### ToolCall: one tool use, kept for display (lines 51–66)

```python
51  @dataclass
52  class ToolCall:
53      """One tool invocation, kept for display: what was asked, what came back."""
54  
55      id: str
56      name: str
57      input: dict[str, Any]
58      result: dict[str, Any] | None = None
59      # Chart PNG bytes captured at call time. Chart files are named after
60      # their title, so a later chart with the same title would overwrite the
61      # file on disk -- holding the bytes keeps earlier answers intact.
62      chart_png: bytes | None = None
63  
64      @property
65      def is_error(self) -> bool:
66          return bool(self.result and "error" in self.result)
```

Records what the model asked for (`name`, `input`), what came back (`result`), and the chart image bytes if there was one. `is_error` is a computed property: True when the result has an `"error"` key.

The PNG bytes are kept because chart files are named after their title: a second chart with the same title would overwrite the file on disk.

### AgentTurn: everything one question produced (lines 69–76)

```python
69  @dataclass
70  class AgentTurn:
71      """Everything one user question produced."""
72  
73      text: str
74      tool_calls: list[ToolCall] = field(default_factory=list)
75      messages: list[dict[str, Any]] = field(default_factory=list)  # full history incl. this turn
76      stopped_early: bool = False  # hit max_steps before the model gave a final answer
```

The final answer text, the list of tool calls, the full message history (passed back in on the next question so follow-ups work), and whether it hit the step limit.

### _block_to_dict() (lines 79–90)

```python
79  def _block_to_dict(block: Any) -> dict[str, Any]:
80      """Anthropic SDK content block (or a plain dict) -> plain dict for the history."""
81      if isinstance(block, dict):
82          return block
83      btype = getattr(block, "type", None)
84      if btype == "text":
85          return {"type": "text", "text": block.text}
86      if btype == "tool_use":
87          return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
88      if hasattr(block, "model_dump"):
89          return block.model_dump(exclude_none=True)
90      raise TypeError(f"Unrecognized content block: {block!r}")
```

The SDK returns content blocks as objects (`TextBlock`, `ToolUseBlock`). This converts them into plain dicts so the history can be stored, printed as JSON, and sent back to the API unchanged.

### execute_tool(): name → Python call (lines 93–103)

```python
 93  def execute_tool(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
 94      """Dispatch one tool call. Never raises: failures become {'error': ...}."""
 95      fn = TOOL_FUNCTIONS.get(name)
 96      if fn is None:
 97          return {"error": f"Unknown tool '{name}'. Available: {sorted(TOOL_FUNCTIONS)}"}
 98      try:
 99          return fn(**tool_input)
100      except TypeError as exc:  # wrong/missing arguments from the model
101          return {"error": f"Bad arguments for {name}: {exc}"}
102      except Exception as exc:  # noqa: BLE001 -- the model should see it, not the user crash
103          return {"error": f"{name} failed: {type(exc).__name__}: {exc}"}
```

- Line 95: look up the function by name.
- Line 99: `fn(**tool_input)` unpacks the model's argument dict into keyword arguments. The model's `{"query": "SELECT ..."}` becomes `run_sql_query(query="SELECT ...")`.
- Lines 100-103: if the model passes a wrong argument name (`TypeError`) or the tool crashes, return an error dict. **This function never raises**, so one bad tool call can't take down the app.

> **Breakpoint, line 99:** The single best breakpoint in the project: every tool call passes through here. Inspect `name` and `tool_input`.

### run_agent(): setup (lines 106–135)

```python
106  def run_agent(
107      question: str,
108      history: list[dict[str, Any]] | None = None,
109      *,
110      client: Any = None,
111      model: str = DEFAULT_MODEL,
112      max_steps: int = DEFAULT_MAX_STEPS,
113      max_tokens: int = 2000,
114      on_tool_start: Callable[[ToolCall], None] | None = None,
115      on_tool_end: Callable[[ToolCall], None] | None = None,
116  ) -> AgentTurn:
117      """Answer one question, calling tools as the model decides.
118  
119      Args:
120          question: the user's message.
121          history: prior messages from earlier turns (AgentTurn.messages), for
122              follow-up questions. Not mutated.
123          client: an anthropic.Anthropic-compatible client. Created from the
124              environment (ANTHROPIC_API_KEY) if omitted.
125          on_tool_start / on_tool_end: optional callbacks so a UI can show
126              progress while the loop runs.
127      """
128      if client is None:
129          import anthropic
130  
131          client = anthropic.Anthropic()
132  
133      messages: list[dict[str, Any]] = list(history or [])
134      messages.append({"role": "user", "content": question})
135      calls: list[ToolCall] = []
```

- Lines 128-131: if no client was passed in, create a real Anthropic client. `anthropic.Anthropic()` reads `ANTHROPIC_API_KEY` from the environment. Tests and `--offline` pass a fake client here instead.
- Line 133: copy the earlier history (`list(...)` makes a copy, so the caller's list isn't changed).
- Line 134: add the new question as a user message.

### The loop, step 1: ask the model (lines 137–143)

```python
137      for _ in range(max_steps):
138          response = client.messages.create(
139              model=model, max_tokens=max_tokens, system=SYSTEM_PROMPT,
140              tools=TOOLS, messages=messages,
141          )
142          content = [_block_to_dict(b) for b in response.content]
143          messages.append({"role": "assistant", "content": content})
```

Each pass sends the **entire** conversation so far, plus the system prompt and tool descriptions. The API is stateless: it remembers nothing between calls, so all context has to be sent every time.

Line 143 appends the model's reply to the history.

> **Breakpoint, line 138:** Inspect `messages` to see exactly what is being sent to Claude this round. After stepping over, inspect `response.content` and `response.stop_reason`.

### The loop, step 2: done yet? (lines 145–148)

```python
145          tool_uses = [b for b in content if b["type"] == "tool_use"]
146          if response.stop_reason != "tool_use" or not tool_uses:
147              text = "\n\n".join(b["text"] for b in content if b["type"] == "text").strip()
148              return AgentTurn(text=text, tool_calls=calls, messages=messages)
```

`stop_reason == "tool_use"` means the model wants tools run. Anything else (normally `"end_turn"`) means it has written its final answer: join the text blocks and return.

> **Breakpoint, line 146:** Check `stop_reason`. This is where a turn ends.

### The loop, step 3: run the requested tools (lines 150–168)

```python
150          results = []
151          for block in tool_uses:
152              call = ToolCall(id=block["id"], name=block["name"], input=dict(block["input"] or {}))
153              if on_tool_start:
154                  on_tool_start(call)
155              call.result = execute_tool(call.name, call.input)
156              chart_path = call.result.get("chart_path") if isinstance(call.result, dict) else None
157              if chart_path and Path(chart_path).exists():
158                  call.chart_png = Path(chart_path).read_bytes()
159              calls.append(call)
160              if on_tool_end:
161                  on_tool_end(call)
162              results.append({
163                  "type": "tool_result",
164                  "tool_use_id": call.id,
165                  "content": json.dumps(call.result, default=str),
166                  **({"is_error": True} if call.is_error else {}),
167              })
168          messages.append({"role": "user", "content": results})
```

For each tool the model asked for (it can ask for several at once):

- Line 152: wrap it in a `ToolCall`.
- Lines 153-154 and 160-161: call the UI's callbacks. This is how Streamlit shows "Ran a SQL query…" live.
- Line 155: run it.
- Lines 156-158: if it produced a chart, read the PNG bytes into memory.
- Lines 162-167: build a `tool_result` block. `tool_use_id` links the result to the request; `is_error` tells the model the call failed.

Line 168 sends all results back **as a user message**. That's how the API works: tool results come from the user side. Then the loop goes round again.

> **Breakpoint, line 165:** Inspect the JSON the model is about to receive as this tool's result.

### Out of steps (lines 170–177)

```python
170      # Out of steps. The history now ends on tool results with no answer, so
171      # append a short assistant note to keep it valid for the next turn.
172      note = (
173          f"I stopped after {max_steps} tool-calling steps without reaching a final answer. "
174          "Try a narrower question, or ask me to continue."
175      )
176      messages.append({"role": "assistant", "content": [{"type": "text", "text": note}]})
177      return AgentTurn(text=note, tool_calls=calls, messages=messages, stopped_early=True)
```

Reached only if the model called tools 10 times without answering. Adds a closing assistant message so the history stays valid for the next question, and flags `stopped_early`.


## `app.py`

The Streamlit chat UI. Streamlit runs this whole file top to bottom on every click and every message.

### Docstring and imports (lines 1–25)

```python
 1  """
 2  Streamlit chat UI for bi-agent.
 3  
 4      pip install -r requirements.txt -r requirements-agent.txt -r requirements-app.txt
 5      python -m bi_agent.cli load-data          # or use the button in the sidebar
 6      export ANTHROPIC_API_KEY=...              # or paste it in the sidebar
 7      streamlit run app.py
 8  
 9  Ask a question in plain English; the agent picks tools, writes its own SQL,
10  and the UI shows its work: every query it ran (with the rows it got back),
11  every chart it rendered, and the final written answer. Follow-up questions
12  keep the conversation context ("now break that down by state").
13  """
14  from __future__ import annotations
15  
16  import os
17  import sqlite3
18  from pathlib import Path
19  
20  import pandas as pd
21  import streamlit as st
22  
23  from bi_agent import db
24  from bi_agent.agent import DEFAULT_MODEL, ToolCall, run_agent
25  from bi_agent.config import get_settings
```

`app.py` is a plain script, not a function. Keep that in mind for everything below.

### Constants and page setup (lines 27–41)

```python
27  EXAMPLE_QUESTIONS = [
28      "Which product categories bring in the most revenue? Show me a chart.",
29      "Chart monthly order volume over time.",
30      "Which states have the highest average delivery delay?",
31      "How do customers feel about the health_beauty category?",
32  ]
33  
34  TOOL_LABELS = {
35      "get_schema": "Looked up the database schema",
36      "run_sql_query": "Ran a SQL query",
37      "create_chart_from_query": "Built a chart",
38      "get_review_sentiment_summary": "Read review sentiment",
39  }
40  
41  st.set_page_config(page_title="BI Agent · Olist", page_icon="📊", layout="wide")
```

The example questions, friendly names for each tool, and `st.set_page_config` (tab title, icon, wide layout). `set_page_config` must be the first Streamlit command in the script.

### Helper functions (lines 44–87)

```python
44  # -- Data status helpers --------------------------------------------------
45  
46  
47  def db_status() -> dict:
48      """Is the database built, and how many reviews are sentiment-scored?"""
49      path = Path(get_settings().db_path)
50      if not path.exists():
51          return {"exists": False}
52      try:
53          conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
54          orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
55          scored = conn.execute("SELECT COUNT(*) FROM review_sentiment").fetchone()[0]
56          conn.close()
57      except sqlite3.Error:
58          return {"exists": False}
59      return {"exists": orders > 0, "orders": orders, "scored": scored}
60  
61  
62  def build_database() -> dict:
63      from bi_agent.load_data import load_all
64  
65      return load_all()
66  
67  
68  def score_some_reviews(limit: int) -> dict:
69      from bi_agent import sentiment
70  
71      conn = db.connect(get_settings().db_path)
72      try:
73          analyzer = sentiment.get_sentiment_analyzer(get_settings().sentiment_backend)
74          counts = sentiment.score_reviews(conn, analyzer, limit=limit)
75          counts["backend"] = analyzer.backend_name
76          return counts
77      finally:
78          conn.close()
79  
80  
81  def get_client(api_key: str):
82      # Tests inject a fake client through session state; real runs build one.
83      if "client_override" in st.session_state:
84          return st.session_state["client_override"]
85      import anthropic
86  
87      return anthropic.Anthropic(api_key=api_key)
```

- `db_status()` (47-59): opens the database read-only and counts orders and scored reviews. Any error means "not built yet".
- `build_database()` and `score_some_reviews()`: what the two sidebar buttons run. They import `load_all` and `sentiment` inside the function so the app starts quickly.
- `get_client()` (81-87): returns a fake client if a test put one in session state, otherwise a real Anthropic client with your key.

> **Breakpoint, line 83:** Stops once per question. Confirms whether a fake client or the real one with your key is used.

### render_tool_call(): show one step (lines 90–137)

```python
 90  # -- Rendering one tool call ---------------------------------------------
 91  
 92  
 93  def render_tool_call(call: ToolCall, *, show_chart: bool = True) -> None:
 94      label = TOOL_LABELS.get(call.name, call.name)
 95      if call.is_error:
 96          st.markdown(f"**{label}**: failed")
 97          if "query" in call.input:
 98              st.code(call.input["query"], language="sql", wrap_lines=True)
 99          st.caption(f"⚠️ {call.result['error']}")
100          return
101  
102      st.markdown(f"**{label}**")
103      result = call.result or {}
104  
105      if call.name == "get_schema":
106          names = [t["name"] for t in result.get("tables", [])]
107          st.caption(", ".join(names) if names else "No tables found.")
108  
109      elif call.name == "run_sql_query":
110          st.code(result.get("sql") or call.input.get("query", ""), language="sql", wrap_lines=True)
111          rows = result.get("rows", [])
112          if rows:
113              df = pd.DataFrame(rows, columns=result.get("columns"))
114              numeric = {c: st.column_config.NumberColumn(format="localized")
115                         for c in df.select_dtypes("number").columns}
116              st.dataframe(df, hide_index=True, width="stretch", column_config=numeric,
117                           height=min(38 + 35 * len(rows), 320))
118          note = f"{result.get('row_count', len(rows))} rows"
119          if result.get("truncated"):
120              note += " (truncated at the row limit)"
121          st.caption(note)
122  
123      elif call.name == "create_chart_from_query":
124          st.code(call.input.get("query", ""), language="sql", wrap_lines=True)
125          if show_chart and call.chart_png:
126              st.image(call.chart_png)
127  
128      elif call.name == "get_review_sentiment_summary":
129          counts = result.get("counts", {})
130          scope = [f"{k}={v}" for k, v in (result.get("filters") or {}).items() if v]
131          st.caption(f"Scope: {', '.join(scope) or 'all reviews'} · "
132                     f"{result.get('reviews_scored_in_scope', 0)} scored reviews")
133          cols = st.columns(3)
134          for col, key in zip(cols, ("positive", "neutral", "negative")):
135              col.metric(key.title(), counts.get(key, 0))
136          for ex in result.get("example_comments", [])[:3]:
137              st.caption(f"*{ex['label']}*: “{ex['comment']}”")
```

Draws one entry of "Show work". Errors show the failed SQL and the error message (95-100). Otherwise one branch per tool:

- SQL: the query (`st.code` with SQL highlighting) and the rows as a table. Lines 114-115 give every numeric column thousands separators.
- Chart: the query and the image.
- Sentiment: three counters and up to three example comments.

### render_assistant(): show a past answer (lines 140–150)

```python
140  def render_assistant(text: str, calls: list[ToolCall]) -> None:
141      charts = [c for c in calls if c.chart_png and not c.is_error]
142      for c in charts:
143          st.image(c.chart_png)
144      st.markdown(text or "_(no answer text)_")
145      if calls:
146          with st.expander(f"Show work: {len(calls)} step{'s' if len(calls) != 1 else ''}"):
147              for i, call in enumerate(calls):
148                  if i:
149                      st.divider()
150                  render_tool_call(call, show_chart=False)
```

Used when redrawing earlier messages: charts first, then the text, then the steps inside a collapsed "Show work" expander.

### Session state: what survives a rerun (lines 153–158)

```python
153  # -- Session state --------------------------------------------------------
154  
155  ss = st.session_state
156  ss.setdefault("chat", [])        # display log: {"role", "text", "calls"}
157  ss.setdefault("history", [])     # Anthropic-format message history
158  ss.setdefault("pending", None)   # question queued by an example button
```

Because the script reruns from the top every time, normal variables are reset on every click. `st.session_state` is a dict that survives reruns for one browser tab.

- `chat`: what to draw (text plus ToolCall objects).
- `history`: the raw Anthropic-format messages, passed back to `run_agent`.
- `pending`: a question queued by clicking an example button.

`setdefault` only sets a value the first time.

> **Breakpoint, line 156:** Stops on every rerun. Inspect `st.session_state` to see the stored conversation.

### Sidebar (lines 161–197)

```python
161  # -- Sidebar ---------------------------------------------------------------
162  
163  with st.sidebar:
164      st.header("Settings")
165      env_key = os.getenv("ANTHROPIC_API_KEY", "")
166      api_key = env_key or st.text_input(
167          "Anthropic API key", type="password",
168          help="Used only for this session. Set ANTHROPIC_API_KEY to skip this.",
169      )
170      if env_key:
171          st.caption("Using ANTHROPIC_API_KEY from the environment.")
172      model = st.text_input("Model", value=DEFAULT_MODEL)
173  
174      st.header("Data")
175      status = db_status()
176      if not status["exists"]:
177          st.warning("The database hasn't been built yet.")
178          if st.button("Build database from raw_data/", type="primary"):
179              with st.spinner("Loading the Olist CSVs into SQLite…"):
180                  result = build_database()
181              st.success(f"Loaded {result['total_rows']:,} rows.")
182              st.rerun()
183      else:
184          st.caption(f"✅ {status['orders']:,} orders loaded")
185          st.caption(f"💬 {status['scored']:,} reviews sentiment-scored")
186          if st.button("Score 1,000 more reviews",
187                       help="Needed for sentiment questions. Uses the transformer model if "
188                            "installed, otherwise the fast lexicon fallback."):
189              with st.spinner("Scoring reviews…"):
190                  counts = score_some_reviews(1000)
191              st.success(f"Scored {counts['scored']:,} with the {counts['backend']} backend.")
192              st.rerun()
193  
194      st.divider()
195      if st.button("New conversation", disabled=not ss.chat):
196          ss.chat, ss.history = [], []
197          st.rerun()
```

- 165-171: use the key from the environment or `.env` if present; otherwise show a password box.
- 172: the model name box, pre-filled with `DEFAULT_MODEL`.
- 175-192: database status plus the Build and Score buttons. `st.button` returns True only on the rerun right after it was clicked, so the code under it runs once per click. `st.rerun()` then restarts the script so the sidebar shows the new counts.
- 195-197: New conversation clears both lists.

### Main panel: redraw history, read the question (lines 200–223)

```python
200  # -- Main panel ------------------------------------------------------------
201  
202  st.title("📊 BI Agent")
203  st.caption("Ask questions about ~99k real Brazilian e-commerce orders (Olist, 2016–2018). "
204             "The agent writes its own SQL, charts the results, and reads customer reviews.")
205  
206  for msg in ss.chat:
207      with st.chat_message(msg["role"]):
208          if msg["role"] == "user":
209              st.markdown(msg["text"])
210          else:
211              render_assistant(msg["text"], msg["calls"])
212  
213  typed = st.chat_input("Ask about revenue, orders, delivery, reviews…")
214  question = typed or ss.pending
215  ss.pending = None
216  
217  if not ss.chat and not question:
218      st.markdown("**Try one of these:**")
219      cols = st.columns(2)
220      for i, q in enumerate(EXAMPLE_QUESTIONS):
221          if cols[i % 2].button(q, key=f"example_{i}", width="stretch"):
222              ss.pending = q
223              st.rerun()
```

- 206-211: redraw every earlier message. This happens on every rerun, which is why the chat stays on screen.
- 213: `st.chat_input` returns the typed text on the rerun after you press Enter, otherwise None.
- 214-215: the question is either typed or a queued example. `pending` is cleared so it's used only once.
- 217-223: show example buttons only on an empty chat. Clicking one stores the question in `pending` and reruns, so it arrives here on the next pass.

> **Breakpoint, line 214:** Inspect `typed` and `ss.pending` to see where the question came from.

### Answering: show the question, check prerequisites (lines 225–240)

```python
225  if question:
226      ss.chat.append({"role": "user", "text": question, "calls": []})
227      with st.chat_message("user"):
228          st.markdown(question)
229  
230      with st.chat_message("assistant"):
231          if not status["exists"]:
232              reply = "The database isn't built yet. Use **Build database** in the sidebar first."
233              st.markdown(reply)
234              ss.chat.append({"role": "assistant", "text": reply, "calls": []})
235              st.stop()
236          if not api_key and "client_override" not in ss:
237              reply = "Add your Anthropic API key in the sidebar so I can answer."
238              st.markdown(reply)
239              ss.chat.append({"role": "assistant", "text": reply, "calls": []})
240              st.stop()
```

Adds the question to the chat and draws it. If the database is missing or there's no API key, writes a helpful reply and calls `st.stop()`, which ends this run of the script immediately.

### Answering: run the agent with live progress (lines 242–259)

```python
242          progress = st.status("Working on it…", expanded=True)
243  
244          def on_start(call: ToolCall) -> None:
245              progress.update(label=f"{TOOL_LABELS.get(call.name, call.name)}…")
246  
247          def on_end(call: ToolCall) -> None:
248              with progress:
249                  render_tool_call(call)
250  
251          try:
252              turn = run_agent(question, ss.history, client=get_client(api_key), model=model,
253                               on_tool_start=on_start, on_tool_end=on_end)
254          except Exception as exc:  # noqa: BLE001 -- API/auth/network problems
255              progress.update(label="Something went wrong", state="error", expanded=False)
256              reply = f"I couldn't reach the model: `{type(exc).__name__}: {exc}`"
257              st.markdown(reply)
258              ss.chat.append({"role": "assistant", "text": reply, "calls": []})
259              st.stop()
```

- 242: a status box that starts expanded.
- 244-249: the two callbacks given to `run_agent`. `on_start` changes the box label; `on_end` draws the finished step inside the box. These are what make steps appear one by one while the agent works.
- 251-253: **the call into the agent**. Everything in `agent.py` happens inside this line.
- 254-259: API problems (bad key, wrong model name, no internet) become a readable message instead of a crash.

> **Breakpoint, line 252:** Step into (F11) here to follow the question into the agent loop.

### Answering: show the result and save it (lines 261–270)

```python
261          steps = len(turn.tool_calls)
262          progress.update(label=f"Done: {steps} step{'s' if steps != 1 else ''}",
263                          state="complete", expanded=False)
264          for c in turn.tool_calls:
265              if c.chart_png and not c.is_error:
266                  st.image(c.chart_png)
267          st.markdown(turn.text)
268  
269      ss.history = turn.messages
270      ss.chat.append({"role": "assistant", "text": turn.text, "calls": turn.tool_calls})
```

Collapse the status box, show any charts and the answer, then save the new history (269) and the display entry (270) into session state so they survive the next rerun.


## `bi_agent/cli.py`

Command-line entry point. Useful for testing each piece without the AI.

### Docstring and imports (lines 1–16)

```python
 1  """
 2  Command-line entry point.
 3  
 4  Usage:
 5      python -m bi_agent.cli load-data
 6      python -m bi_agent.cli schema
 7      python -m bi_agent.cli query "SELECT category_english, COUNT(*) FROM v_order_items_full GROUP BY 1"
 8      python -m bi_agent.cli chart --query "..." --type bar --x category_english --y total --title "..."
 9      python -m bi_agent.cli score-reviews --limit 200 --backend auto
10      python -m bi_agent.cli sentiment --category health_beauty
11  """
12  from __future__ import annotations
13  
14  import argparse
15  import json
16  import sys
```

Lists every command. Run `python -m bi_agent.cli --help` to see them.

### The command functions (lines 19–66)

```python
19  def cmd_load_data(_args: argparse.Namespace) -> None:
20      from .load_data import load_all
21  
22      print(json.dumps(load_all(), indent=2))
23  
24  
25  def cmd_schema(_args: argparse.Namespace) -> None:
26      from .tools import get_schema
27  
28      print(json.dumps(get_schema(), indent=2))
29  
30  
31  def cmd_query(args: argparse.Namespace) -> None:
32      from .tools import run_sql_query
33  
34      print(json.dumps(run_sql_query(query=args.sql), indent=2))
35  
36  
37  def cmd_chart(args: argparse.Namespace) -> None:
38      from .tools import create_chart_from_query
39  
40      result = create_chart_from_query(
41          query=args.query, chart_type=args.type, x=args.x, y=args.y,
42          series=args.series, title=args.title or "", x_label=args.x_label, y_label=args.y_label,
43      )
44      print(json.dumps(result, indent=2))
45  
46  
47  def cmd_score_reviews(args: argparse.Namespace) -> None:
48      from . import db, sentiment
49      from .config import get_settings
50  
51      settings = get_settings()
52      conn = db.connect(settings.db_path)
53      try:
54          analyzer = sentiment.get_sentiment_analyzer(args.backend)
55          print(f"Using backend: {analyzer.backend_name}", file=sys.stderr)
56          counts = sentiment.score_reviews(conn, analyzer, limit=args.limit, rescore=args.rescore)
57      finally:
58          conn.close()
59      print(json.dumps(counts, indent=2))
60  
61  
62  def cmd_sentiment(args: argparse.Namespace) -> None:
63      from .tools import get_review_sentiment_summary
64  
65      result = get_review_sentiment_summary(category=args.category, state=args.state)
66      print(json.dumps(result, indent=2))
```

One small function per command. Each imports what it needs, calls the same functions the agent uses, and prints the result as JSON. So `cli query "..."` runs exactly the guarded `run_sql_query` the model uses.

### build_parser() (lines 69–102)

```python
 69  def build_parser() -> argparse.ArgumentParser:
 70      parser = argparse.ArgumentParser(prog="bi_agent", description=__doc__)
 71      sub = parser.add_subparsers(dest="command", required=True)
 72  
 73      sub.add_parser("load-data", help="Load the Olist CSVs (raw_data/) into SQLite.").set_defaults(func=cmd_load_data)
 74      sub.add_parser("schema", help="Print the queryable schema.").set_defaults(func=cmd_schema)
 75  
 76      p_query = sub.add_parser("query", help="Run a read-only SQL query.")
 77      p_query.add_argument("sql")
 78      p_query.set_defaults(func=cmd_query)
 79  
 80      p_chart = sub.add_parser("chart", help="Run a query and render a chart PNG.")
 81      p_chart.add_argument("--query", required=True)
 82      p_chart.add_argument("--type", required=True, choices=["bar", "hbar", "line", "stacked_bar"])
 83      p_chart.add_argument("--x", required=True)
 84      p_chart.add_argument("--y", required=True)
 85      p_chart.add_argument("--series", default=None)
 86      p_chart.add_argument("--title", default="")
 87      p_chart.add_argument("--x-label", dest="x_label", default=None)
 88      p_chart.add_argument("--y-label", dest="y_label", default=None)
 89      p_chart.set_defaults(func=cmd_chart)
 90  
 91      p_score = sub.add_parser("score-reviews", help="Batch-score review sentiment into the DB.")
 92      p_score.add_argument("--limit", type=int, default=200)
 93      p_score.add_argument("--backend", default="auto", choices=["auto", "transformer", "lexicon"])
 94      p_score.add_argument("--rescore", action="store_true", help="Re-score reviews that already have a sentiment row.")
 95      p_score.set_defaults(func=cmd_score_reviews)
 96  
 97      p_sent = sub.add_parser("sentiment", help="Summarize already-scored review sentiment.")
 98      p_sent.add_argument("--category", default=None)
 99      p_sent.add_argument("--state", default=None)
100      p_sent.set_defaults(func=cmd_sentiment)
101  
102      return parser
```

Sets up `argparse` subcommands. `set_defaults(func=...)` attaches the function to run for each subcommand.

### main() (lines 105–116)

```python
105  def main(argv: list[str] | None = None) -> None:
106      parser = build_parser()
107      args = parser.parse_args(argv)
108      try:
109          args.func(args)
110      except Exception as exc:
111          print(f"Error: {exc}", file=sys.stderr)
112          sys.exit(1)
113  
114  
115  if __name__ == "__main__":
116      main()
```

Parses arguments and calls the chosen function. Errors print a one-line message and exit with code 1.


## `agent_demo.py`

The same agent from the terminal: one question in, one answer out.

### Docstring and imports (lines 1–24)

```python
 1  """
 2  Live agentic demo (command line): a text-to-SQL-and-chart BI assistant over
 3  the Olist dataset. The model sees the schema tool, decides what SQL to
 4  write, decides whether a chart is warranted, and can pull in review
 5  sentiment -- all from one flat tool list, no hand-written NL-to-SQL parser.
 6  
 7  The loop itself lives in bi_agent/agent.py so the Streamlit app (app.py)
 8  and this script share it.
 9  
10  Requires:
11      pip install -r requirements-agent.txt
12      export ANTHROPIC_API_KEY=...
13      python -m bi_agent.cli load-data
14      python -m bi_agent.cli score-reviews   # optional, only needed for sentiment questions
15  
16  Usage:
17      python agent_demo.py "Which product categories bring in the most revenue?"
18      python agent_demo.py "Chart monthly order volume over time"
19      python agent_demo.py "How do customers feel about the health_beauty category?"
20  """
21  from __future__ import annotations
22  
23  import json
24  import sys
```

Usage examples at the top.

### main() (lines 27–55)

```python
27  def main(question: str) -> None:
28      try:
29          import anthropic  # noqa: F401
30      except ImportError:
31          print(
32              "The 'anthropic' package isn't installed. Run:\n"
33              "  pip install -r requirements-agent.txt\n"
34              "This demo is optional -- the pipeline and tools work without it."
35          )
36          sys.exit(1)
37  
38      from bi_agent.agent import run_agent
39  
40      def log_call(call) -> None:
41          print(f"[tool call] {call.name}({json.dumps(call.input)})", file=sys.stderr)
42  
43      def log_result(call) -> None:
44          if call.is_error:
45              print(f"[tool error] {call.result['error']}", file=sys.stderr)
46          elif call.result.get("chart_path"):
47              print(f"[chart saved] {call.result['chart_path']}", file=sys.stderr)
48  
49      turn = run_agent(question, on_tool_start=log_call, on_tool_end=log_result)
50      print(turn.text)
51  
52  
53  if __name__ == "__main__":
54      q = " ".join(sys.argv[1:]) or "Which product categories bring in the most revenue? Show me a chart."
55      main(q)
```

Checks the `anthropic` package is installed, defines two small logging callbacks (tool calls and errors go to stderr), calls `run_agent`, and prints the answer.


## `scripts/debug_agent.py`

New: runs one agent turn outside Streamlit, for stepping through in a debugger.

### Docstring and path setup (lines 1–35)

```python
 1  """
 2  Run ONE agent turn outside Streamlit, so you can step through it in a
 3  debugger and see every message that goes to and from the model.
 4  
 5      python scripts/debug_agent.py --offline
 6          A scripted fake model plays back realistic tool calls against the
 7          REAL database. No API key, no cost, same result every run --
 8          the easiest way to step through the whole pipeline.
 9  
10      python scripts/debug_agent.py "Which states order the most?"
11          Real Claude (needs ANTHROPIC_API_KEY in .env or the environment).
12  
13  Either way it prints each tool call as it happens, then saves the full
14  message history to debug_last_turn.json so you can read exactly what the
15  model saw and said.
16  
17  Good breakpoints (see the walkthrough for why):
18      bi_agent/agent.py      run_agent()       -- the loop: model call, tool dispatch
19      bi_agent/agent.py      execute_tool()    -- where a tool name becomes a Python call
20      bi_agent/query.py      validate_query()  -- the SQL safety checks
21      bi_agent/query.py      run_sql_query()   -- the read-only connection + row cap
22      bi_agent/charts.py     create_chart()    -- rows -> PNG
23  """
24  from __future__ import annotations
25  
26  import argparse
27  import json
28  import sys
29  from pathlib import Path
30  
31  ROOT = Path(__file__).resolve().parent.parent
32  sys.path.insert(0, str(ROOT))           # so `import bi_agent` works from anywhere
33  sys.path.insert(0, str(ROOT / "tests"))  # for the fake model used by --offline
34  
35  from bi_agent.agent import ToolCall, run_agent  # noqa: E402
```

Lines 31-33 add the project root and `tests/` to Python's import path, so the script runs from anywhere and can borrow the fake model.

### The offline fake model (lines 37–54)

```python
37  REVENUE_SQL = (
38      "SELECT category_english, ROUND(SUM(revenue), 0) AS total "
39      "FROM v_order_items_full GROUP BY category_english ORDER BY total DESC LIMIT 5"
40  )
41  
42  
43  def offline_client():
44      """A fake model that 'decides' the same steps a real one usually would."""
45      from fake_llm import FakeClient, response, text, tool_use
46  
47      return FakeClient([
48          response(text("Let me look at the schema first."), tool_use("t1", "get_schema")),
49          response(tool_use("t2", "run_sql_query", query=REVENUE_SQL)),
50          response(tool_use("t3", "create_chart_from_query", query=REVENUE_SQL, chart_type="hbar",
51                            x="category_english", y="total", title="Top 5 categories by revenue",
52                            y_label="Revenue (R$)")),
53          response(text("(Scripted answer) Health & beauty is the top category by revenue.")),
54      ])
```

A scripted stand-in for Claude that "decides" to call get_schema, then run a SQL query, then draw a chart, then answer. The tools run for real against your real database; only the model's decisions are scripted.

### main() (lines 57–88)

```python
57  def short(obj, limit: int = 300) -> str:
58      s = json.dumps(obj, default=str)
59      return s if len(s) <= limit else s[:limit] + f"... ({len(s):,} chars)"
60  
61  
62  def main() -> None:
63      parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
64      parser.add_argument("question", nargs="?", default="Which 5 product categories bring in the most revenue? Show me a chart.")
65      parser.add_argument("--offline", action="store_true", help="use the scripted fake model (no API key)")
66      parser.add_argument("--model", default=None, help="override the model name")
67      args = parser.parse_args()
68  
69      client = offline_client() if args.offline else None  # None -> run_agent builds a real client
70      kwargs = {"model": args.model} if args.model else {}
71  
72      def on_start(call: ToolCall) -> None:
73          print(f"\n-> tool call: {call.name}({short(call.input)})")
74  
75      def on_end(call: ToolCall) -> None:
76          print(f"<- result:    {short(call.result)}")
77  
78      print(f"Question: {args.question}  [{'offline fake model' if args.offline else 'real Claude'}]")
79      turn = run_agent(args.question, client=client, on_tool_start=on_start, on_tool_end=on_end, **kwargs)
80  
81      print("\n=== Final answer ===\n" + turn.text)
82      out = ROOT / "debug_last_turn.json"
83      out.write_text(json.dumps(turn.messages, indent=2, default=str))
84      print(f"\n{len(turn.tool_calls)} tool calls, {len(turn.messages)} messages. Full history saved to {out.name}")
85  
86  
87  if __name__ == "__main__":
88      main()
```

Parses arguments, prints each tool call and a shortened result, then writes the full message history to `debug_last_turn.json`. Open that file to read the exact conversation between the code and the model.

## Things you'll notice while debugging

### Possible crash hiding the real error

`bi_agent/query.py:127`

If `sqlite3.connect()` on line 127 itself fails, `conn` never gets a value, and the `conn.close()` in the `finally` on line 136 raises `UnboundLocalError`, which hides the real error. It's rare because line 122 checks the file exists first. The fix is to set `conn = None` before the `try` and close only if it's not None.

### get_schema can create an empty database file

`bi_agent/query.py:86`

`sqlite3.connect()` creates the file if it's missing. Calling `get_schema` before `load-data` leaves an empty `bi_agent.db` behind and returns tables with no columns. Opening with `?mode=ro`, as `run_sql_query` does, would avoid it.

### The keyword blocklist rejects some harmless queries

`bi_agent/query.py:36`

`REPLACE` is blocked because of `REPLACE INTO`, but SQLite also has a harmless `REPLACE()` string function, so `SELECT REPLACE(category_english, '_', ' ')` is rejected. Words inside string literals are caught too: `WHERE order_status = 'update'` would fail. The read-only connection is the real protection, so the list could be loosened safely.

### The lexicon scorer misreads negations

`bi_agent/sentiment.py:123`

It checks substrings. "não recomendo" contains both "não recomendo" (negative) and "recomendo" (positive), so it scores 1 vs 1 and comes out neutral. "não gostei" has the same problem, and "bom" also matches inside longer words. Another reason to use the transformer for anything you present.

### Example comments are biased toward confident ones

`bi_agent/tools.py:136`

`ORDER BY rs.score DESC LIMIT 6` picks the six most confident reviews regardless of label. With mostly positive data, the examples can all be positive even when you asked about complaints. Picking two per label would give a fairer sample.

### Large results cost tokens

`bi_agent/agent.py:165`

Every tool result is sent back to the model as JSON. A 500-row result is tens of thousands of characters, and it's re-sent with every later request in the conversation. Watch `len(json.dumps(call.result))` here if answers get slow or expensive.

