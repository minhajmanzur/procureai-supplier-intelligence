"""Pydantic models shared by the parser, the agents and the API."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

FeeBasis = Literal["fixed", "percent_of_goods", "unquantified"]


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float


class Fee(BaseModel):
    """A charge found in the quotation text, beyond the unit price."""

    kind: str = Field(description="freight, handling, packaging, tooling, documentation, duty, insurance, surcharge, bank")
    basis: FeeBasis
    amount: Optional[float] = Field(default=None, description="Fixed amount in the quote currency, if basis is fixed")
    percent: Optional[float] = Field(default=None, description="Percent of goods value, if basis is percent_of_goods")
    evidence: str = Field(description="The sentence in the quotation that states this charge")
    per_quantity: Optional[float] = Field(default=None, description="If the charge is per piece (e.g. per 1000 pcs): the quantity `amount` was computed for")


class ParsedQuote(BaseModel):
    source_file: str = ""
    supplier_name: Optional[str] = None
    currency: str = "USD"
    line_items: list[LineItem] = []
    quoted_total: Optional[float] = Field(default=None, description="Headline total stated on the quote")
    lead_time_days: Optional[int] = None
    incoterm: Optional[str] = None
    advance_payment_pct: float = Field(default=0.0, description="Percent of order value due before shipment")
    net_payment_days: int = Field(default=0, description="Credit days on the remaining balance")
    moq: Optional[int] = None
    validity_days: Optional[int] = None
    warranty_months: Optional[int] = None
    price_escalation_clause: Optional[str] = Field(default=None, description="Sentence allowing the price to change")
    fees: list[Fee] = []
    parse_method: str = "rules"
    parse_warnings: list[str] = []


class Weights(BaseModel):
    cost: float = 0.45
    delivery_risk: float = 0.25
    reliability: float = 0.20
    lead_time: float = 0.10

    def normalised(self) -> "Weights":
        total = self.cost + self.delivery_risk + self.reliability + self.lead_time
        if total <= 0:
            return Weights()
        return Weights(
            cost=self.cost / total,
            delivery_risk=self.delivery_risk / total,
            reliability=self.reliability / total,
            lead_time=self.lead_time / total,
        )


class RFQLine(BaseModel):
    item: str
    quantity: int = Field(gt=0)
    category: Optional[str] = None


class RFQ(BaseModel):
    """A request for quotation: one item (item + quantity), or several via `lines`."""

    item: str = "Industrial servo motor 400W"
    category: Optional[str] = None
    quantity: int = 500
    needed_in_days: Optional[int] = 30
    weights: Weights = Weights()
    lines: list[RFQLine] = []

    @model_validator(mode="after")
    def _summarise_lines(self) -> "RFQ":
        if self.lines:
            self.item = self.lines[0].item if len(self.lines) == 1 else f"{len(self.lines)} items"
            self.quantity = sum(line.quantity for line in self.lines)
        return self

    def all_lines(self) -> list[RFQLine]:
        return self.lines or [RFQLine(item=self.item, quantity=self.quantity, category=self.category)]
