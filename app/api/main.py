"""FastAPI service.  Run:  uvicorn app.api.main:app --reload"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError
from sqlalchemy import select

from app.agents.graph import NoUsableQuotes, run_comparison
from app.analysis.history import overview, supplier_history
from app.bootstrap import bootstrap
from app.config import ROOT
from app.db import ComparisonRun, Supplier, session_scope
from app.ingestion.parser import IMAGE_TYPES, load_document
from app.ml import risk as risk_model
from app.ml.features import load_orders, supplier_profile
from app.report import build_report
from app.schemas import RFQ, RFQLine, Weights

SAMPLES = ROOT / "samples"
SAMPLE_SETS = {
    "servo_motors": {
        "label": "Servo motors (5 quotations)",
        "folder": SAMPLES / "quotes",
        "rfq": RFQ(item="Industrial servo motor 400W", quantity=500, needed_in_days=30),
    },
    "fasteners": {
        "label": "Industrial fasteners (5 PDFs)",
        "folder": SAMPLES / "fasteners_pdf",
        "rfq": RFQ(item="Hex bolt M10x50, DIN 933, grade 8.8, zinc plated", quantity=50000, needed_in_days=45),
    },
    "scanned": {
        "label": "Scanned quotations, OCR (4 scans + 1 photo)",
        "folder": SAMPLES / "scanned",
        "rfq": RFQ(item="Hex bolt M10x50, DIN 933, grade 8.8, zinc plated", quantity=50000, needed_in_days=45),
    },
    "fastener_kit": {
        "label": "Fastener kit, 3 items (5 PDFs)",
        "folder": SAMPLES / "fastener_kit_pdf",
        "rfq": RFQ(
            needed_in_days=45,
            lines=[
                RFQLine(item="Hex bolt M10x50, DIN 933, grade 8.8, zinc plated", quantity=50000),
                RFQLine(item="Hex nut M10, DIN 934, grade 8, zinc plated", quantity=50000),
                RFQLine(item="Flat washer M10, DIN 125, zinc plated", quantity=100000),
            ],
        ),
    },
}
STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_FILES, MAX_BYTES = 10, 15 * 1024 * 1024  # scans and photos are large


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap()
    yield


app = FastAPI(title="ProcureAI", description="Autonomous procurement and supplier intelligence", version="0.1.0", lifespan=lifespan)


def _compare(documents: list[dict], rfq: RFQ) -> dict:
    try:
        result = run_comparison(documents, rfq)
    except NoUsableQuotes as exc:
        raise HTTPException(422, str(exc)) from exc
    with session_scope() as s:
        run = ComparisonRun(item=rfq.item, quantity=rfq.quantity, recommended_supplier=result["recommendation"]["supplier_name"], result=result)
        s.add(run)
        s.flush()
        result["run_id"] = run.id
    return result


def _rfq(item: str, quantity: int, needed_in_days: int | None, category: str | None, weights: str | None, lines: str | None = None) -> RFQ:
    try:
        w = Weights(**json.loads(weights)) if weights else Weights()
        rows = [RFQLine(**row) for row in json.loads(lines)] if lines else []
        return RFQ(item=item, quantity=quantity, needed_in_days=needed_in_days, category=category or None, weights=w, lines=rows)
    except (ValidationError, json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(422, f"Invalid request: {exc}") from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/compare")
async def compare(
    files: list[UploadFile] = File(..., description="Supplier quotations: .txt, .pdf (text or scanned) or a photo (.jpg, .png)"),
    item: str = Form("Industrial servo motor 400W"),
    quantity: int = Form(500, gt=0),
    needed_in_days: int | None = Form(30),
    category: str | None = Form(None),
    weights: str | None = Form(None, description='JSON, e.g. {"cost":0.5,"delivery_risk":0.3,"reliability":0.1,"lead_time":0.1}'),
    lines: str | None = Form(None, description='Several items instead of item/quantity. JSON, e.g. [{"item":"Hex bolt M10x50","quantity":50000},{"item":"Hex nut M10","quantity":50000}]'),
) -> dict:
    """Upload supplier quotations; the agents compare them and recommend one."""
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"Upload at most {MAX_FILES} quotations per run.")
    documents = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"{f.filename} is larger than 15 MB.")
        try:
            documents.append(load_document(f.filename or "quote", data))
        except Exception as exc:  # unreadable or corrupt PDF
            raise HTTPException(422, f"Could not read {f.filename}: {exc}") from exc
    rfq = _rfq(item, quantity, needed_in_days, category, weights, lines)
    return await run_in_threadpool(_compare, documents, rfq)


@app.get("/api/samples")
def samples() -> list[dict]:
    """Bundled sample quotation sets, each with the RFQ it answers."""
    return [{"name": name, "label": s["label"], "rfq": s["rfq"].model_dump()} for name, s in SAMPLE_SETS.items()]


@app.post("/api/compare/samples/{name}")
def compare_sample_set(name: str, rfq: RFQ | None = None) -> dict:
    """Run the comparison on a bundled sample set (send an RFQ to override its defaults)."""
    if name not in SAMPLE_SETS:
        raise HTTPException(404, f"Unknown sample set. Available: {', '.join(SAMPLE_SETS)}.")
    sample = SAMPLE_SETS[name]
    files = sorted(p for p in sample["folder"].iterdir() if p.suffix.lower() in (".txt", ".pdf", *IMAGE_TYPES))
    documents = [load_document(p.name, p.read_bytes()) for p in files]
    return _compare(documents, rfq or sample["rfq"])


@app.post("/api/compare/samples", include_in_schema=False)
def compare_samples(rfq: RFQ | None = None) -> dict:
    return compare_sample_set("servo_motors", rfq)


@app.get("/api/suppliers")
def suppliers() -> list[dict]:
    """All suppliers with their reliability record."""
    with session_scope() as s:
        orders = load_orders(s)
        rows = list(s.scalars(select(Supplier).order_by(Supplier.name)))
        return [
            {"id": x.id, "name": x.name, "country": x.country, "years_active": x.years_active, "certifications": x.certifications,
             **supplier_profile(orders, x.id)}
            for x in rows
        ]


@app.get("/api/suppliers/{supplier_id}")
def supplier(supplier_id: int, category: str | None = None) -> dict:
    with session_scope() as s:
        x = s.get(Supplier, supplier_id)
        if x is None:
            raise HTTPException(404, "Supplier not found.")
        return {"id": x.id, "name": x.name, "country": x.country, "distance_km": x.distance_km, "years_active": x.years_active,
                "certifications": x.certifications, **supplier_profile(load_orders(s), x.id, category)}


def _supplier_dict(x: Supplier) -> dict:
    return {"id": x.id, "name": x.name, "country": x.country, "distance_km": x.distance_km, "years_active": x.years_active, "certifications": x.certifications}


@app.get("/api/dashboard")
def dashboard() -> dict:
    """Portfolio totals and one performance row per supplier."""
    with session_scope() as s:
        rows = [_supplier_dict(x) for x in s.scalars(select(Supplier).order_by(Supplier.name))]
        return overview(load_orders(s), rows)


@app.get("/api/suppliers/{supplier_id}/history")
def supplier_history_endpoint(supplier_id: int) -> dict:
    """Quarterly on-time rate, lead times, price trend and recent orders for one supplier."""
    with session_scope() as s:
        x = s.get(Supplier, supplier_id)
        if x is None:
            raise HTTPException(404, "Supplier not found.")
        orders = load_orders(s)
        return {"supplier": _supplier_dict(x), "profile": supplier_profile(orders, x.id), **supplier_history(orders, x.id)}


@app.get("/suppliers", include_in_schema=False)
def suppliers_page() -> FileResponse:
    return FileResponse(STATIC / "suppliers.html")


@app.get("/api/runs")
def runs(limit: int = 20) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(ComparisonRun).order_by(ComparisonRun.id.desc()).limit(min(limit, 100)))
        return [{"id": r.id, "created_at": r.created_at, "item": r.item, "quantity": r.quantity, "recommended_supplier": r.recommended_supplier} for r in rows]


@app.get("/api/runs/{run_id}")
def run_detail(run_id: int) -> dict:
    with session_scope() as s:
        r = s.get(ComparisonRun, run_id)
        if r is None:
            raise HTTPException(404, "Run not found.")
        return {**r.result, "run_id": r.id}


@app.get("/api/runs/{run_id}/report.pdf")
def run_report(run_id: int) -> Response:
    """The comparison as a PDF report: recommendation, comparison, cost evidence and assumptions."""
    with session_scope() as s:
        r = s.get(ComparisonRun, run_id)
        if r is None:
            raise HTTPException(404, "Run not found.")
        pdf = build_report({**r.result, "run_id": r.id}, generated_at=r.created_at)
    headers = {"Content-Disposition": f'attachment; filename="procureai-comparison-{run_id}.pdf"'}
    return Response(pdf, media_type="application/pdf", headers=headers)


@app.get("/api/model")
def model() -> dict:
    """Delivery-risk model card: held-out metrics and feature importance."""
    return risk_model.model_meta()


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
