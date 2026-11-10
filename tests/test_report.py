"""PDF comparison report."""
import io
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.agents.graph import run_comparison
from app.bootstrap import bootstrap
from app.ingestion.parser import load_document
from app.report import build_report
from app.schemas import RFQ, RFQLine

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def run(folder: str, rfq: RFQ) -> dict:
    bootstrap()
    files = sorted(p for p in (SAMPLES / folder).iterdir() if p.suffix in (".txt", ".pdf"))
    return run_comparison([load_document(p.name, p.read_bytes()) for p in files], rfq)


def text_of(pdf: bytes) -> str:
    assert pdf.startswith(b"%PDF")
    return " ".join(" ".join((page.extract_text() or "").split()) for page in PdfReader(io.BytesIO(pdf)).pages)


@pytest.fixture(scope="module")
def servo():
    return run("quotes", RFQ())


@pytest.fixture(scope="module")
def kit():
    return run("fastener_kit_pdf", RFQ(needed_in_days=45, lines=[
        RFQLine(item="Hex bolt M10x50, DIN 933, grade 8.8, zinc plated", quantity=50000),
        RFQLine(item="Hex nut M10, DIN 934, grade 8, zinc plated", quantity=50000),
        RFQLine(item="Flat washer M10, DIN 125, zinc plated", quantity=100000),
    ]))


def test_report_states_the_decision_and_every_supplier(servo):
    text = text_of(build_report({**servo, "run_id": 7}))
    assert "RECOMMENDED AWARD" in text and servo["recommendation"]["supplier_name"] in text
    for q in servo["quotes"]:
        assert q["supplier_name"] in text
    assert "run #7" in text and "Page 1" in text


def test_report_carries_the_evidence(servo):
    text = text_of(build_report(servo))
    assert "held" in text and "abnormally low" in text                     # the held quote and why
    assert "one-time tooling charge of USD 1,800.00" in text                # the sentence a hidden charge came from
    assert "Assumptions and method" in text and "ROC AUC" in text
    assert "Negotiation levers" in text and "Agent trace" in text


def test_figures_in_the_report_match_the_result(servo):
    text = text_of(build_report(servo))
    for q in servo["quotes"]:
        assert f"${q['risk_adjusted_usd']:,.0f}" in text
        assert f"${q['cost']['headline_usd']:,.0f}" in text


def test_multi_item_report_has_matrix_and_split(kit):
    text = text_of(build_report(kit))
    assert "Unit price by item" in text and "not quoted" in text
    assert "Split-award option: save" in text and f"${kit['split_award']['total_usd']:,.0f}" in text
    assert "Flat washer M10, DIN 125, zinc plated" in text and "3 of 3" in text and "2 of 3" in text


def test_single_item_report_has_no_multi_item_sections(servo):
    text = text_of(build_report(servo))
    assert "Unit price by item" not in text and "Split-award" not in text


def test_awkward_characters_do_not_break_the_report(servo):
    odd = {**servo, "narrative": "Line one\n\nTom & Jerry <b>unclosed, 5 < 6 > 4, naïve café, 中文, ✓"}
    odd["quotes"] = [{**servo["quotes"][0], "supplier_name": "A & B <Holdings> Öztürk 株式会社"}] + servo["quotes"][1:]
    text = text_of(build_report(odd))
    assert "Tom & Jerry" in text and "A & B <Holdings>" in text


def test_report_endpoint():
    from fastapi.testclient import TestClient

    from app.api.main import app

    with TestClient(app) as client:
        run_id = client.post("/api/compare/samples/servo_motors").json()["run_id"]
        r = client.get(f"/api/runs/{run_id}/report.pdf")
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        assert f"procureai-comparison-{run_id}.pdf" in r.headers["content-disposition"]
        assert r.content.startswith(b"%PDF")
        assert client.get("/api/runs/999999/report.pdf").status_code == 404
