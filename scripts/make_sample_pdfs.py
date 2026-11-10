"""Render the five sample quotations as PDFs, the way suppliers actually send them.

Run:  python scripts/make_sample_pdfs.py      (writes samples/quotes_pdf/*.pdf)

Each PDF uses a different layout (form with label/value grid, ruled table, plain
letter, borderless columns, compact table with notes) so the parser is exercised on
the text that PDF extraction really produces, not on hand-typed text files.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
OUT = SAMPLES / "quotes_pdf"          # servo motor RFQ
FASTENERS = SAMPLES / "fasteners_pdf"  # industrial fasteners RFQ
KIT = SAMPLES / "fastener_kit_pdf"     # three-item RFQ: bolts, nuts, washers

base = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=base["Normal"], fontSize=9.5, leading=13)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
COMPANY = ParagraphStyle("company", parent=base["Title"], fontSize=16, leading=20, alignment=0, spaceAfter=2)
TITLE = ParagraphStyle("title", parent=base["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=6)
RIGHT = ParagraphStyle("right", parent=BODY, alignment=TA_RIGHT)


def p(text: str, style=BODY) -> Paragraph:
    return Paragraph(text, style)


def grid(rows, widths, header_bg="#e9eef5", ruled=True) -> Table:
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (-3, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]
    if ruled:
        style += [("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa5b1")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg))]
    else:
        style += [("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black)]
    t.setStyle(TableStyle(style))
    return t


def label_value(rows, widths=(45 * mm, 110 * mm)) -> Table:
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9.5), ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 3)]))
    return t


def build(name: str, story: list, pagesize=A4, out: Path | None = None) -> None:
    doc = SimpleDocTemplate(str((out or OUT) / name), pagesize=pagesize, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=name.removesuffix(".pdf"))
    doc.build(story)


def shenzhen() -> None:
    """Form layout: label/value grid, numbered terms."""
    build("01_shenzhen_dynatek.pdf", [
        p("Shenzhen Dynatek Motion Co. Ltd", COMPANY),
        p("No. 88 Fuyong Industrial Avenue, Bao'an District, Shenzhen 518103, China", SMALL),
        p("QUOTATION", TITLE),
        label_value([
            ["Supplier:", "Shenzhen Dynatek Motion Co. Ltd"],
            ["Quote Ref:", "DTK-Q-2611-0457"],
            ["Date:", "2026-11-06"],
            ["To:", "Procurement Department"],
        ]),
        Spacer(1, 8),
        label_value([
            ["Item:", "Industrial Servo Motor 400W, model DT-SM400"],
            ["Quantity:", "500 pcs"],
            ["Unit Price:", "USD 38.90"],
            ["Total Amount:", "USD 19,450.00"],
        ]),
        p("Terms and Conditions", TITLE),
        p("1. Price basis: EXW Shenzhen factory. Freight and insurance are not included and are to be arranged by the buyer."),
        p("2. A one-time tooling charge of USD 1,800.00 applies to first orders of this model."),
        p("3. Minimum order quantity: 600 pcs."),
        p("4. Payment: 100% advance by T/T before production."),
        p("5. Lead time: 6 weeks after receipt of payment."),
        p("6. Export packing in wooden crates: USD 320.00."),
        p("7. Quotation valid for 15 days."),
        p("8. Warranty: 12 months."),
    ])


def bavaria() -> None:
    """Ruled table in EUR; a charge appears as its own table row."""
    build("02_bavaria_antriebstechnik.pdf", [
        p("Bavaria Antriebstechnik GmbH", COMPANY),
        p("Industriestrasse 14, 86153 Augsburg, Germany", SMALL),
        p("ANGEBOT / QUOTATION No. BA-2026-11873", TITLE),
        p("Date: 05.11.2026"),
        p("Currency: EUR"),
        Spacer(1, 8),
        grid([
            ["Pos", "Description", "Qty", "Unit Price", "Amount"],
            ["1", "Servo motor 400 W, type BA-S4-400", "500", "44.20", "22,100.00"],
            ["2", "Export packaging (pallets)", "1", "350.00", "350.00"],
        ], [12 * mm, 82 * mm, 18 * mm, 26 * mm, 30 * mm]),
        Spacer(1, 6),
        p("<b>Total: EUR 22,450.00</b>", RIGHT),
        Spacer(1, 10),
        p("Delivery terms: DAP buyer's warehouse (Incoterms 2020). Transport and transit insurance are included in the unit price."),
        p("Delivery time: 24 days from order confirmation."),
        p("Payment terms: net 60 days from invoice date."),
        p("Warranty: 24 months."),
        p("Offer valid for 30 days."),
    ])


def midwest() -> None:
    """A plain business letter: everything is in prose, wrapped across lines."""
    build("03_midwest_precision.pdf", [
        p("Midwest Precision Drives Inc.", COMPANY),
        p("4410 Industrial Parkway, Rockford, IL 61109 &nbsp;|&nbsp; sales@midwestprecisiondrives.example", SMALL),
        Spacer(1, 14),
        p("November 5, 2026"),
        Spacer(1, 8),
        p("<b>Re: Quote MPD-88214 - 400W servo motors</b>"),
        Spacer(1, 8),
        p("Hi,"),
        Spacer(1, 6),
        p("Thanks for the RFQ. We can supply 500 units of our MPD-400 servo motor (400W) at $47.50 per unit, for a total of $23,750.00."),
        Spacer(1, 6),
        p("That price is DDP to your dock: shipping, insurance and all duties are included, with no additional charges."),
        Spacer(1, 6),
        p("We can ship within 14 days of PO. Payment is Net 30. All units carry a 36-month warranty. This quote is valid for 45 days."),
        Spacer(1, 12),
        p("Best regards,"),
        p("Dana Whitfield"),
        p("Inside Sales"),
    ], pagesize=LETTER)


def anatolia() -> None:
    """Borderless column table, bulleted commercial terms."""
    build("04_anatolia_elektrik.pdf", [
        p("ANATOLIA ELEKTRIK MOTOR A.S.", COMPANY),
        p("Organize Sanayi Bolgesi, Bursa, Turkiye", SMALL),
        p("PROFORMA QUOTATION AEM/PQ/26/3391", TITLE),
        p("04 Nov 2026"),
        p("Vendor: Anatolia Elektrik Motor A.S."),
        Spacer(1, 8),
        grid([
            ["Description", "Qty", "Unit Price (USD)", "Total (USD)"],
            ["AC Servo Motor 400W AEM-SV40", "500", "41.00", "20,500.00"],
        ], [80 * mm, 20 * mm, 34 * mm, 34 * mm], ruled=False),
        Spacer(1, 6),
        p("Subtotal: USD 20,500.00", RIGHT),
        p("Commercial terms", TITLE),
        p("- Incoterm: CIF Port of New York."),
        p("- A handling surcharge of 3.5% is applied to all export orders."),
        p("- Documentation and certificate of origin fee: USD 250.00."),
        p("- Payment: 30% advance, balance net 30 days."),
        p("- Delivery: 30 days after receipt of advance payment."),
        p("- Prices are subject to adjustment in line with the LME copper index at time of shipment."),
        p("- Validity: this quotation is valid for 7 days."),
        p("- Warranty 18 months."),
    ])


def pune() -> None:
    """Compact ruled table followed by a notes paragraph."""
    build("05_pune_rotodyne.pdf", [
        p("PUNE ROTODYNE PVT LTD", COMPANY),
        p("Plot 221, MIDC Chakan, Pune 410501, India &nbsp;|&nbsp; GSTIN 27AAACR5055K1Z9", SMALL),
        Spacer(1, 8),
        label_value([["Quotation:", "PR/Q/1126/092"], ["Date:", "07-Nov-2026"], ["Supplier:", "Pune Rotodyne Pvt Ltd"]]),
        Spacer(1, 8),
        grid([
            ["Sr", "Item", "Qty", "Rate (USD)", "Amount (USD)"],
            ["1", "Servo Motor 400W RD-400S (equivalent)", "500", "29.50", "14,750.00"],
        ], [12 * mm, 84 * mm, 18 * mm, 26 * mm, 30 * mm], header_bg="#f3e9dc"),
        Spacer(1, 6),
        p("<b>Grand Total: USD 14,750.00</b>", RIGHT),
        p("Notes", TITLE),
        p("Price is FOB Nhava Sheva. Ocean freight, insurance and destination customs duty are to buyer's account. "
          "Dispatch within 10 days from PO. Payment 50% advance, balance against bill of lading. "
          "Bank charges outside India are borne by the buyer. Validity 10 days. Warranty 6 months."),
    ])


# --- Industrial fasteners RFQ: 50,000 x hex bolt M10x50, DIN 933, grade 8.8, zinc plated ---


def ningbo() -> None:
    """Form layout, priced per 1000 pieces, with a per-1000 plating surcharge in the terms."""
    build("01_ningbo_eastbolt.pdf", [
        p("Ningbo Eastbolt Fastener Co. Ltd", COMPANY),
        p("No. 216 Binhai 4th Road, Hangzhou Bay New Zone, Ningbo 315336, China", SMALL),
        p("QUOTATION", TITLE),
        label_value([
            ["Supplier:", "Ningbo Eastbolt Fastener Co. Ltd"],
            ["Quote Ref:", "NEB-26-11-3087"],
            ["Date:", "2026-11-09"],
        ]),
        Spacer(1, 8),
        label_value([
            ["Item:", "Hex bolt M10x50, DIN 933, grade 8.8, plain finish"],
            ["Quantity:", "50,000 pcs"],
            ["Price per 1000 pcs:", "USD 89.00"],
            ["Total Amount:", "USD 4,450.00"],
        ]),
        p("Terms and Conditions", TITLE),
        p("1. Price basis: FOB Ningbo port."),
        p("2. Zinc plating surcharge: USD 6.50 per 1000 pcs."),
        p("3. Mill test certificate (EN 10204 3.1): USD 95.00 per shipment."),
        p("4. Export packing in cartons on fumigated pallets: USD 180.00."),
        p("5. Minimum order quantity: 60,000 pcs (full carton multiples)."),
        p("6. Payment: 100% T/T in advance."),
        p("7. Lead time: 45 days after receipt of payment."),
        p("8. Quotation valid for 20 days."),
    ], out=FASTENERS)


def rheinland() -> None:
    """Ruled table in EUR, priced per 100 pieces; everything included."""
    build("02_rheinland_schraubenwerk.pdf", [
        p("Rheinland Schraubenwerk GmbH", COMPANY),
        p("Hafenstrasse 41, 41460 Neuss, Germany", SMALL),
        p("ANGEBOT / QUOTATION No. RS-2026-40112", TITLE),
        p("Date: 06.11.2026"),
        p("Currency: EUR"),
        Spacer(1, 8),
        grid([
            ["Pos", "Description", "Qty", "Price per 100", "Amount"],
            ["1", "Hex bolt DIN 933 M10x50, 8.8, zinc plated", "50,000", "12.40", "6,200.00"],
        ], [12 * mm, 80 * mm, 20 * mm, 28 * mm, 28 * mm]),
        Spacer(1, 6),
        p("<b>Total: EUR 6,200.00</b>", RIGHT),
        Spacer(1, 10),
        p("Delivery terms: DAP buyer's warehouse (Incoterms 2020). Packaging and transport are included in the price."),
        p("Inspection certificate 3.1 according to EN 10204 is supplied free of charge."),
        p("Delivery time: 26 days from order confirmation."),
        p("Payment terms: net 45 days from invoice date."),
        p("Offer valid for 30 days."),
    ], out=FASTENERS)


def great_lakes() -> None:
    """A plain letter from a domestic distributor: price each, freight on top."""
    build("03_great_lakes_bolt.pdf", [
        p("Great Lakes Bolt &amp; Screw Co.", COMPANY),
        p("1820 Harbor Industrial Drive, Cleveland, OH 44114 &nbsp;|&nbsp; quotes@greatlakesbolt.example", SMALL),
        Spacer(1, 14),
        p("November 9, 2026"),
        Spacer(1, 8),
        p("<b>Re: Quote GL-55731 - M10x50 hex bolts</b>"),
        Spacer(1, 8),
        p("Thank you for the opportunity to quote."),
        Spacer(1, 6),
        p("We can supply 50,000 pieces of M10x50 hex cap screw, DIN 933, class 8.8, zinc plated, at $0.129 each, for a total of $6,450.00. "
          "All material is in stock and comes with full lot traceability at no additional cost."),
        Spacer(1, 6),
        p("Terms are FOB Origin, Cleveland. Freight is not included in the above and is estimated at $285.00 by LTL carrier."),
        Spacer(1, 6),
        p("We can deliver within 10 business days of PO. Payment is Net 30. This quote is valid for 30 days."),
        Spacer(1, 12),
        p("Sincerely,"),
        p("Marcus Feld"),
        p("Account Manager"),
    ], pagesize=LETTER, out=FASTENERS)


def kaohsiung() -> None:
    """Borderless columns, priced per 1000, bulleted terms with a documentation fee."""
    build("04_kaohsiung_precision.pdf", [
        p("KAOHSIUNG PRECISION FASTENERS CORP", COMPANY),
        p("No. 57 Bengong 5th Road, Gangshan District, Kaohsiung 820, Taiwan", SMALL),
        p("PROFORMA QUOTATION KPF/Q/26/1184", TITLE),
        p("09 Nov 2026"),
        p("Vendor: Kaohsiung Precision Fasteners Corp"),
        Spacer(1, 8),
        grid([
            ["Description", "Qty", "USD / 1000 pcs", "Total (USD)"],
            ["Hex bolt M10x50 DIN 933 Gr. 8.8 Zn", "50,000", "104.50", "5,225.00"],
        ], [80 * mm, 22 * mm, 34 * mm, 32 * mm], ruled=False),
        Spacer(1, 6),
        p("Subtotal: USD 5,225.00", RIGHT),
        p("Commercial terms", TITLE),
        p("- Incoterm: CIF Los Angeles."),
        p("- PPAP documentation package: USD 150.00."),
        p("- Payment: 30% advance, balance net 30 days."),
        p("- Delivery: 38 days after receipt of advance payment."),
        p("- Validity: this quotation is valid for 30 days."),
    ], out=FASTENERS)


def ludhiana() -> None:
    """Compact table with a notes paragraph: very low price, fast promise, loose terms."""
    build("05_ludhiana_forge.pdf", [
        p("LUDHIANA FORGE &amp; FASTENERS PVT LTD", COMPANY),
        p("C-112 Focal Point Phase VIII, Ludhiana 141010, Punjab, India &nbsp;|&nbsp; GSTIN 03AABCL7719R1ZT", SMALL),
        Spacer(1, 8),
        label_value([["Quotation:", "LFF/EXP/2611/047"], ["Date:", "10-Nov-2026"], ["Supplier:", "Ludhiana Forge & Fasteners Pvt Ltd"]]),
        Spacer(1, 8),
        grid([
            ["Sr", "Item", "Qty", "Rate per 1000 (USD)", "Amount (USD)"],
            ["1", "Hex bolt M10x50 Gr. 8.8 or equivalent, zinc", "50,000", "68.00", "3,400.00"],
        ], [10 * mm, 78 * mm, 20 * mm, 36 * mm, 28 * mm], header_bg="#f3e9dc"),
        Spacer(1, 6),
        p("<b>Grand Total: USD 3,400.00</b>", RIGHT),
        p("Notes", TITLE),
        p("Price is FOB Mundra. Ocean freight, insurance and destination customs duty are to buyer's account. "
          "Dispatch within 15 days from PO. Payment 50% advance, balance against bill of lading. "
          "Rates are subject to revision in line with steel wire rod prices at the time of order. "
          "Bank charges outside India are borne by the buyer. Validity 10 days."),
    ], out=FASTENERS)


# --- Fastener kit RFQ (three items): 50,000 bolts M10x50, 50,000 nuts M10, 100,000 washers M10 ---


def kit_kaohsiung() -> None:
    build("01_kaohsiung_precision.pdf", [
        p("KAOHSIUNG PRECISION FASTENERS CORP", COMPANY),
        p("No. 57 Bengong 5th Road, Gangshan District, Kaohsiung 820, Taiwan", SMALL),
        p("PROFORMA QUOTATION KPF/Q/26/1203", TITLE),
        p("10 Nov 2026"),
        p("Vendor: Kaohsiung Precision Fasteners Corp"),
        Spacer(1, 8),
        grid([
            ["Description", "Qty", "USD / 1000 pcs", "Total (USD)"],
            ["Hex bolt M10x50 DIN 933 Gr. 8.8 Zn", "50,000", "104.50", "5,225.00"],
            ["Hex nut M10 DIN 934 Gr. 8 Zn", "50,000", "31.00", "1,550.00"],
            ["Flat washer M10 DIN 125-A 200HV Zn", "100,000", "10.60", "1,060.00"],
        ], [80 * mm, 22 * mm, 34 * mm, 32 * mm], ruled=False),
        Spacer(1, 6),
        p("Subtotal: USD 7,835.00", RIGHT),
        p("Commercial terms", TITLE),
        p("- Incoterm: CIF Los Angeles."),
        p("- PPAP documentation package: USD 150.00."),
        p("- Payment: 30% advance, balance net 30 days."),
        p("- Delivery: 38 days after receipt of advance payment."),
        p("- Validity: this quotation is valid for 30 days."),
    ], out=KIT)


def kit_ningbo() -> None:
    build("02_ningbo_eastbolt.pdf", [
        p("Ningbo Eastbolt Fastener Co. Ltd", COMPANY),
        p("No. 216 Binhai 4th Road, Hangzhou Bay New Zone, Ningbo 315336, China", SMALL),
        p("QUOTATION NEB-26-11-3102", TITLE),
        p("Supplier: Ningbo Eastbolt Fastener Co. Ltd"),
        p("Date: 2026-11-10"),
        Spacer(1, 8),
        grid([
            ["No.", "Item", "Qty (pcs)", "Price per 1000", "Amount (USD)"],
            ["1", "Hex bolt M10x50, DIN 933, grade 8.8, plain", "50,000", "92.00", "4,600.00"],
            ["2", "Hex nut M10, DIN 934, grade 8, plain", "50,000", "24.50", "1,225.00"],
            ["3", "Plain washer M10, DIN 125, plain", "100,000", "8.90", "890.00"],
        ], [12 * mm, 78 * mm, 24 * mm, 28 * mm, 30 * mm]),
        Spacer(1, 6),
        p("<b>Total Amount: USD 6,715.00</b>", RIGHT),
        p("Terms and Conditions", TITLE),
        p("1. Price basis: FOB Ningbo port."),
        p("2. Zinc plating surcharge: USD 6.50 per 1000 pcs, all items."),
        p("3. Mill test certificate (EN 10204 3.1): USD 95.00 per shipment."),
        p("4. Export packing in cartons on fumigated pallets: USD 260.00."),
        p("5. Payment: 100% T/T in advance."),
        p("6. Lead time: 45 days after receipt of payment."),
        p("7. Quotation valid for 20 days."),
    ], out=KIT)


def kit_rheinland() -> None:
    build("03_rheinland_schraubenwerk.pdf", [
        p("Rheinland Schraubenwerk GmbH", COMPANY),
        p("Hafenstrasse 41, 41460 Neuss, Germany", SMALL),
        p("ANGEBOT / QUOTATION No. RS-2026-40158", TITLE),
        p("Date: 09.11.2026"),
        p("Currency: EUR"),
        Spacer(1, 8),
        grid([
            ["Pos", "Description", "Qty", "Price per 100", "Amount"],
            ["1", "Hex bolt DIN 933 M10x50, 8.8, zinc plated", "50,000", "12.40", "6,200.00"],
            ["2", "Hex nut DIN 934 M10, cl. 8, zinc plated", "50,000", "3.40", "1,700.00"],
            ["3", "Washer DIN 125-A M10, 200HV, zinc plated", "100,000", "1.20", "1,200.00"],
        ], [12 * mm, 80 * mm, 20 * mm, 28 * mm, 28 * mm]),
        Spacer(1, 6),
        p("<b>Total: EUR 9,100.00</b>", RIGHT),
        Spacer(1, 10),
        p("Delivery terms: DAP buyer's warehouse (Incoterms 2020). Packaging and transport are included in the price."),
        p("Inspection certificate 3.1 according to EN 10204 is supplied free of charge."),
        p("Delivery time: 26 days from order confirmation."),
        p("Payment terms: net 45 days from invoice date."),
        p("Offer valid for 30 days."),
    ], out=KIT)


def kit_great_lakes() -> None:
    """Quotes two of the three items, in its own words, and adds one nobody asked for."""
    build("04_great_lakes_bolt.pdf", [
        p("Great Lakes Bolt &amp; Screw Co.", COMPANY),
        p("1820 Harbor Industrial Drive, Cleveland, OH 44114 &nbsp;|&nbsp; quotes@greatlakesbolt.example", SMALL),
        Spacer(1, 10),
        p("<b>Quote GL-55790</b> &nbsp; November 10, 2026"),
        Spacer(1, 8),
        grid([
            ["Part", "Qty", "Price each", "Extended"],
            ["Hex cap screw M10x50 class 8.8 zinc", "50,000", "$0.129", "$6,450.00"],
            ["Hex nut M10 class 8 zinc", "50,000", "$0.038", "$1,900.00"],
            ["Thread locker, 250 ml bottle", "12", "$18.50", "$222.00"],
        ], [84 * mm, 22 * mm, 30 * mm, 32 * mm]),
        Spacer(1, 6),
        p("<b>Total: $8,572.00</b>", RIGHT),
        Spacer(1, 10),
        p("We do not stock DIN 125 washers in this quantity and are unable to quote that line. "
          "Thread locker is suggested for this joint and can be removed from the order."),
        Spacer(1, 6),
        p("Terms are FOB Origin, Cleveland. Freight is not included in the above and is estimated at $310.00 by LTL carrier."),
        Spacer(1, 6),
        p("We can deliver within 10 business days of PO. Payment is Net 30. This quote is valid for 30 days."),
    ], pagesize=LETTER, out=KIT)


def kit_monterrey() -> None:
    build("05_monterrey_tornillos.pdf", [
        p("MONTERREY TORNILLOS INDUSTRIALES SA DE CV", COMPANY),
        p("Av. Miguel Aleman 4500, Parque Industrial Apodaca, 66600 Apodaca, N.L., Mexico", SMALL),
        Spacer(1, 8),
        label_value([["Cotizacion / Quote:", "MTI-2611-0932"], ["Date:", "10-Nov-2026"], ["Supplier:", "Monterrey Tornillos Industriales SA de CV"]]),
        Spacer(1, 8),
        grid([
            ["Sr", "Item", "Qty", "Rate per 1000 (USD)", "Amount (USD)"],
            ["1", "Flat washer M10 DIN 125 zinc plated", "100,000", "10.20", "1,020.00"],
            ["2", "Hex nut M10 DIN 934 grade 8 zinc plated", "50,000", "29.50", "1,475.00"],
            ["3", "Hex bolt M10x50 DIN 933 grade 8.8 zinc plated", "50,000", "118.00", "5,900.00"],
        ], [10 * mm, 78 * mm, 20 * mm, 36 * mm, 28 * mm], header_bg="#e6f0e4"),
        Spacer(1, 6),
        p("<b>Grand Total: USD 8,395.00</b>", RIGHT),
        p("Notes", TITLE),
        p("Price is DAP Laredo, Texas; freight to the border is included. Delivery within 14 days from PO. "
          "Payment net 30 days. Validity 30 days."),
    ], out=KIT)


if __name__ == "__main__":
    folders = (OUT, FASTENERS, KIT)
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
    for make in (shenzhen, bavaria, midwest, anatolia, pune, ningbo, rheinland, great_lakes, kaohsiung, ludhiana,
                 kit_kaohsiung, kit_ningbo, kit_rheinland, kit_great_lakes, kit_monterrey):
        make()
    for folder in folders:
        print(f"Wrote {len(list(folder.glob('*.pdf')))} PDFs to {folder}")
