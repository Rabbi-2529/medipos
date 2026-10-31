"""The small framework every report is built on.

A report declares which filters it takes and returns a Result (columns, rows, totals). One view
and one template render any report as a page, an Excel file or a print/PDF version.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

ZERO = Decimal("0")
ROW_LIMIT = 5000  # rows shown on the page; the Excel file has them all


@dataclass
class Col:
    key: str
    label: str
    kind: str = "text"  # text | money | int | pct | date
    total: bool = False

    @property
    def numeric(self):
        return self.kind in ("money", "int", "pct")


@dataclass
class Link:
    """A cell that opens a page (an invoice, a customer…)."""

    text: str
    url: str

    def __str__(self):
        return self.text


@dataclass
class Result:
    columns: list
    rows: list
    totals: dict = field(default_factory=dict)
    tiles: list = field(default_factory=list)  # [(label, value, kind)] shown above the table
    note: str = ""


@dataclass
class Filters:
    start: date
    end: date
    outlet_ids: list
    outlet: object = None
    customer: object = None
    supplier: object = None
    medicine: object = None
    category: str = ""
    cashier: object = None
    group: str = ""
    option: str = ""


class Report:
    key = ""
    title = ""
    group = ""
    icon = "bi-file-earmark-bar-graph"
    description = ""
    permission = "report.list"
    filters = ("period", "outlet")
    default_days = None  # None = this month so far; otherwise the last N days
    groupings = ()  # [(value, label)] for the "Group by" filter
    options = ()  # [(value, label)] for the report's own extra choice
    option_label = ""
    needs = None  # a filter that must be chosen before the report runs ("customer", "medicine", …)

    def run(self, f):  # pragma: no cover - every report overrides it
        raise NotImplementedError

    def default_period(self):
        today = timezone.localdate()
        if self.default_days:
            return today - timedelta(days=self.default_days), today
        return today.replace(day=1), today


def totals_for(columns, rows):
    totals = {}
    for col in columns:
        if col.total:
            totals[col.key] = sum((row.get(col.key) or 0 for row in rows), ZERO if col.kind == "money" else 0)
    return totals


def pct(part, whole):
    return (Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.1")) if whole else None


GROUPS = [
    ("sales", _("Sales"), "bi-receipt", "sky"),
    ("profit", _("Profit"), "bi-graph-up-arrow", "teal"),
    ("stock", _("Stock & purchases"), "bi-box-seam", "orange"),
    ("ledgers", _("Customers & suppliers"), "bi-people", "violet"),
    ("staff", _("Staff & registers"), "bi-person-badge", "rose"),
]
