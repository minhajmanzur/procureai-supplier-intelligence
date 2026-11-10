# ProcureAI

Autonomous procurement and supplier intelligence. Upload supplier quotations and a team
of agents works out what each one really costs, how likely it is to arrive late, whether
the price looks unusual, and which supplier to award.

## Quick start (Windows)

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.api.main:app --reload
```

Open http://localhost:8000 and pick a sample set, or drop in your own quotations. API docs
are at http://localhost:8000/docs. The first start seeds demo data and trains the model.

| Page | What it shows |
|---|---|
| `/` | Upload quotations, get the comparison and recommendation |
| `/suppliers` | Supplier dashboard: on-time rate, trend, defects, lead times and price history |
| `/api/runs/{id}/report.pdf` | The comparison as a PDF report (also the **Download report** button on a result) |

Sample sets (regenerate the PDFs with `python scripts/make_sample_pdfs.py`):

| Set | Files | RFQ |
|---|---|---|
| Servo motors | `samples/quotes/*.txt`, same quotes as PDF in `samples/quotes_pdf/` | 500 x servo motor 400W, 30 days |
| Industrial fasteners | `samples/fasteners_pdf/*.pdf` | 50,000 x hex bolt M10x50 DIN 933 gr. 8.8, 45 days |
| Fastener kit (3 items) | `samples/fastener_kit_pdf/*.pdf` | 50,000 bolts + 50,000 nuts + 100,000 washers, 45 days |
| Scanned quotations | `samples/scanned/` (4 image-only PDFs + 1 photo; `python scripts/make_scanned_samples.py`) | same as industrial fasteners |

Other entry points:

```bash
.venv\Scripts\python -m app.cli                 # compare the bundled samples in the terminal
.venv\Scripts\python -m app.cli a.pdf b.txt --qty 200 --item "Planetary gearbox"
.venv\Scripts\python -m app.cli quotes/*.pdf --line "50000:Hex bolt M10x50" --line "50000:Hex nut M10"   # several items
.venv\Scripts\python scripts/show_parse.py samples/fasteners_pdf   # what the parser read from each file
.venv\Scripts\python -m app.cli --report report.pdf   # also write the comparison as a PDF report
.venv\Scripts\python -m app.seed                # regenerate the synthetic history
.venv\Scripts\python -m app.ml.train            # retrain and print held-out metrics
.venv\Scripts\python -m pytest
```

## How it works

```
intake -> supplier_intel -+-> cost_analyst    -+
                          +-> risk_analyst    -+-> recommender -> narrator
                          +-> pricing_auditor -+
```

| Agent | What it does | Code |
|---|---|---|
| intake | Parses each quotation (key/value, tables, prose; text PDFs, scans and photos via OCR; prices each, per 100 or per 1000) into a structured quote | `app/ingestion/parser.py` |
| supplier_intel | Matches the supplier to order history; on-time rate, defect rate, trend | `app/ml/features.py` |
| cost_analyst | Landed cost: stated fees, Incoterm gaps, payment-terms financing, MOQ surplus | `app/analysis/costing.py` |
| risk_analyst | XGBoost probability of late delivery, with the features driving it | `app/ml/` |
| pricing_auditor | Unusual pricing vs. this round's quotes, the market, the supplier's own history | `app/analysis/pricing.py` |
| recommender | Weighted score, ranking, flags, negotiation levers | `app/analysis/recommend.py` |
| narrator | Written summary (Claude if configured, otherwise a template) | `app/agents/llm.py` |

The graph is defined in `app/agents/graph.py` (LangGraph). The three analysts run in parallel.

### Hidden costs

Every cost line is labelled `quoted` (in the headline total), `hidden` (stated in the
terms but left out of the total), `estimated` (left to the buyer by the Incoterm and not
priced at all) or `unquantified`. Each line quotes the sentence it came from.

### PDF report

Every comparison can be exported as a PDF (`app/report.py`) to file with the purchase
order. Page 1 is the decision: the requested items, the recommended award and why, the
split-award option if there is one, a cost chart and the comparison table. The pages
after it are the evidence: each quotation's landed-cost breakdown with the sentence
every charge came from, its flags, what drives its delivery-risk prediction and
negotiation levers, then the assumptions, model accuracy and agent trace.

### Scanned quotations (OCR)

A PDF with no text layer, or an image file (`.jpg`, `.png`, `.tif`, ...), is read by OCR
(`app/ingestion/ocr.py`). It runs locally on CPU with RapidOCR (PP-OCR models on ONNX
Runtime); nothing is sent to an external service. The English recognition model is
downloaded once, the first time a scan is read.

1. The page is read once to find the text and measure how far it is tilted, then
   straightened and read again.
2. Very long lines are cut at a word gap and read in pieces (the model garbles them otherwise).
3. The text boxes are put back into lines with their column gaps, so tables look like
   they do in a text PDF and the ordinary parser reads them.

Quotes read this way are marked `read by OCR` with the OCR confidence. Table rows are
still cross-checked (quantity x price must equal the line total), which catches most
misread digits. Expect roughly 2-4 seconds per page. Handwriting is not supported.

### Several items in one RFQ

An RFQ can list several items (`lines` in the API, the item list in the UI, `--line` in
the CLI). Each quoted line is matched to a requested item by product type and shared
specification tokens (`app/analysis/matching.py`), so suppliers can word, order and price
the items differently.

- An item a supplier did not quote is added at this round's median price, so incomplete
  quotes stay comparable; the quote is flagged.
- A quoted line nobody asked for is left out of the comparison and of the headline total.
- Each item is price-checked against its own category history.
- **Split award** (`app/analysis/split.py`): every way of dividing the items among the
  eligible suppliers is costed with the same landed-cost model. If the best split beats
  the cheapest single award by more than 1%, it is shown as an option next to the
  single-supplier recommendation. Limited to 6 items and 6 suppliers.

### Abnormally low prices

A quote whose price is flagged low by two or more independent checks is **held for
verification**: it is scored and shown, but it cannot be the recommended award and does
not set the cost benchmark for the other quotes.

## Configuration

Copy `.env.example` to `.env`. All settings are in `app/config.py`.

- **PostgreSQL**: the default is a local SQLite file so the project runs with no setup.
  For Postgres, run `docker compose up -d` and set `PROCUREAI_DATABASE_URL`.
- **LLM layer**: set `ANTHROPIC_API_KEY` to enable the extraction fallback for quotations
  the rule-based parser cannot read, and the written summary. All numbers come from the
  deterministic cost model and XGBoost regardless.
- **Cost assumptions**: duty rate, freight rates, cost of capital, cost of delay and FX
  rates are settings, and are returned with every result under `assumptions`.

## Data

Supplier history is synthetic (`app/seed.py`): 16 fictional suppliers (10 motion-control,
6 fastener) and 8,400 purchase orders over three years. Lateness is simulated from each supplier's hidden traits (typical lead time,
quoted buffer, variability) plus order size, shipping mode and season, so the model has
real structure to learn. To use real data, load your own rows into the `suppliers` and
`purchase_orders` tables and retrain.
