"""Branch audits: head office works out one branch's figures for a period, the checklist flags what looks wrong,
the auditor marks each check, writes findings and signs it off."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.setup.services import format_money, get_business_settings, next_code

from .models import AuditCheck, AuditEntry, AuditFinding, BranchAudit

ZERO = Decimal("0")
DISCOUNT_LIMIT = Decimal("10")  # % of the sales before discounts
RETURN_LIMIT = Decimal("5")     # % of sales
LOSS_LIMIT = Decimal("1")       # % of the stock's value
STALE_HOURS = 24                # a register open longer than this was probably not closed

CHECKS = [
    ("cash", gettext_lazy("Cash registers balanced")),
    ("open_registers", gettext_lazy("No register left open")),
    ("discounts", gettext_lazy("Discounts within limits")),
    ("returns", gettext_lazy("Returns within limits")),
    ("below_cost", gettext_lazy("Nothing sold below cost")),
    ("books", gettext_lazy("Sales and returns match the books")),
    ("posted", gettext_lazy("Every sale is in the books")),
    ("balance", gettext_lazy("The books balance")),
    ("stock_loss", gettext_lazy("Stock losses")),
    ("expired", gettext_lazy("No expired stock on the shelves")),
    ("count", gettext_lazy("A stock count was done")),
    ("dues", gettext_lazy("Customer dues")),
    ("transit", gettext_lazy("Transfers on the way")),
    ("activity", gettext_lazy("Deleted records")),
]
CHECK_TITLES = dict(CHECKS)


class AuditError(Exception):
    """A user-facing reason a branch audit can't change."""


def _m(value):
    """Money as exact text for JSON (the money tags read it back)."""
    return f"{Decimal(value or 0):.2f}"


def _pct(part, whole):
    return (Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.1")) if whole else ZERO


def _name(first, last, email):
    return f"{first or ''} {last or ''}".strip() or email or "—"


def branch_figures(outlet, start, end, today=None):
    """Everything the audit looks at, for one branch and period, as JSON-ready values."""
    from apps.accounting.models import CashEntry, JournalEntry
    from apps.accounting.reports import profit_and_loss, trial_balance
    from apps.customers.models import CustomerReceipt
    from apps.inventory.models import StockCount, Transfer, WriteOff
    from apps.purchases.models import Purchase, PurchaseReturn
    from apps.sales.models import CashRegister, Sale, SaleLine, SalePayment, SaleReturn
    from apps.stock.models import BatchStock, StockMovement

    today = today or timezone.localdate()
    sales = Sale.objects.filter(outlet=outlet, sold_at__date__gte=start, sold_at__date__lte=end)
    s = sales.aggregate(count=Count("id"), gross=Sum("subtotal"), line_discount=Sum("line_discount"), discount=Sum("discount"),
                        points=Sum("points_discount"), vat=Sum("vat_amount"), total=Sum("total"), paid=Sum("paid"), cost=Sum("cost_total"))
    s = {k: v or (0 if k == "count" else ZERO) for k, v in s.items()}
    returns = SaleReturn.objects.filter(outlet=outlet, date__gte=start, date__lte=end)
    r = returns.aggregate(count=Count("id"), total=Sum("total"), refunds=Sum("refund_amount"))
    r = {k: v or (0 if k == "count" else ZERO) for k, v in r.items()}
    discounts = s["line_discount"] + s["discount"]

    cashiers = {}
    for row in sales.values("created_by", "created_by__first_name", "created_by__last_name", "created_by__email").annotate(
            n=Count("id"), total=Sum("total"), discount=Sum(F("discount") + F("line_discount"))):
        cashiers[row["created_by"]] = {"name": _name(row["created_by__first_name"], row["created_by__last_name"], row["created_by__email"]),
                                       "sales": row["n"], "total": row["total"] or ZERO, "discount": row["discount"] or ZERO, "returns": ZERO}
    for row in returns.values("created_by", "created_by__first_name", "created_by__last_name", "created_by__email").annotate(t=Sum("total")):
        person = cashiers.setdefault(row["created_by"], {"name": _name(row["created_by__first_name"], row["created_by__last_name"],
                                                                       row["created_by__email"]), "sales": 0, "total": ZERO, "discount": ZERO})
        person["returns"] = row["t"] or ZERO
    below = SaleLine.objects.filter(sale__in=sales, line_total__lt=F("cost_total")).aggregate(n=Count("id"), loss=Sum(F("cost_total") - F("line_total")))

    registers, short, over = [], ZERO, ZERO
    stale_before = timezone.now() - timedelta(hours=STALE_HOURS)
    active = (CashRegister.objects.filter(outlet=outlet, opened_at__date__lte=end)
              .filter(Q(closed_at__isnull=True) | Q(closed_at__date__gte=start)).select_related("opened_by").order_by("opened_at"))
    open_now = stale = 0
    for reg in active:
        diff = (reg.counted_cash - reg.expected_cash) if reg.closed_at and reg.counted_cash is not None and reg.expected_cash is not None else None
        if diff is not None:
            short += -diff if diff < 0 else ZERO
            over += diff if diff > 0 else ZERO
        if reg.closed_at is None:
            open_now += 1
            stale += reg.opened_at < stale_before
        registers.append({"who": reg.opened_by.get_full_name() or reg.opened_by.email,
                          "opened": timezone.localtime(reg.opened_at).strftime("%Y-%m-%d %H:%M"),
                          "closed": timezone.localtime(reg.closed_at).strftime("%Y-%m-%d %H:%M") if reg.closed_at else "",
                          "opening": _m(reg.opening_cash), "expected": _m(reg.expected_cash) if reg.expected_cash is not None else "",
                          "counted": _m(reg.counted_cash) if reg.counted_cash is not None else "", "difference": _m(diff) if diff is not None else ""})

    entries = CashEntry.objects.filter(outlet=outlet, date__gte=start, date__lte=end)
    expenses = entries.filter(kind=CashEntry.Kind.EXPENSE).aggregate(t=Sum("amount"))["t"] or ZERO
    income = entries.filter(kind=CashEntry.Kind.INCOME).aggregate(t=Sum("amount"))["t"] or ZERO
    collected = CustomerReceipt.objects.filter(outlet=outlet, date__gte=start, date__lte=end).aggregate(t=Sum("amount"))["t"] or ZERO

    on_hand = BatchStock.objects.filter(outlet=outlet, quantity__gt=0)
    value = on_hand.aggregate(t=Sum(F("quantity") * F("batch__unit_cost")))["t"] or ZERO
    expired = on_hand.filter(batch__expiry_date__lte=today).aggregate(n=Count("id"), t=Sum(F("quantity") * F("batch__unit_cost")))
    expiring = on_hand.filter(batch__expiry_date__gt=today, batch__expiry_date__lte=today + timedelta(days=30)).aggregate(
        n=Count("id"), t=Sum(F("quantity") * F("batch__unit_cost")))
    labels = dict(StockMovement.Kind.choices)
    movements = [{"kind": str(labels.get(row["kind"], row["kind"])), "quantity": row["q"], "value": _m(row["v"])}
                 for row in StockMovement.objects.filter(outlet=outlet, created_at__date__gte=start, created_at__date__lte=end)
                 .values("kind").annotate(q=Sum("quantity"), v=Sum(F("quantity") * F("unit_cost"))).order_by("kind")]
    write_offs = WriteOff.objects.filter(outlet=outlet, date__gte=start, date__lte=end).aggregate(n=Count("id"), t=Sum("total_cost"))
    counts = [{"number": c.number, "date": c.date.isoformat(), "gain": _m(c.gain_cost), "loss": _m(c.loss_cost)}
              for c in StockCount.objects.filter(outlet=outlet, date__gte=start, date__lte=end).order_by("date")]
    count_loss = sum((Decimal(c["loss"]) for c in counts), ZERO)
    transit = Transfer.objects.filter(to_outlet=outlet, status=Transfer.Status.IN_TRANSIT).aggregate(n=Count("id"), t=Sum("total_cost"))

    purchases = Purchase.objects.filter(outlet=outlet, status=Purchase.Status.RECEIVED, date__gte=start, date__lte=end).aggregate(
        n=Count("id"), t=Sum("total"))
    purchase_returns = PurchaseReturn.objects.filter(outlet=outlet, date__gte=start, date__lte=end).aggregate(t=Sum("total"))["t"] or ZERO

    pl = profit_and_loss(start, end, outlet=outlet)
    sale_ids = list(sales.values_list("pk", flat=True))
    posted = JournalEntry.objects.filter(source_type="sale", source_id__in=sale_ids).count()
    deletions = AuditEntry.objects.filter(action=AuditEntry.Action.DELETE, created_at__date__gte=start, created_at__date__lte=end).count()

    return {
        "sales": {"count": s["count"], "gross": _m(s["gross"]), "discounts": _m(discounts), "points": _m(s["points"]), "vat": _m(s["vat"]),
                  "total": _m(s["total"]), "paid": _m(s["paid"]), "due": _m(s["total"] - s["paid"]), "cost": _m(s["cost"]),
                  "discount_pct": str(_pct(discounts, s["gross"])), "returns_count": r["count"], "returns": _m(r["total"]),
                  "refunds": _m(r["refunds"]), "returns_pct": str(_pct(r["total"], s["total"])), "net": _m(s["total"] - r["total"]),
                  "below_cost": below["n"] or 0, "below_cost_loss": _m(below["loss"])},
        "payments": [{"name": row["account__name"], "amount": _m(row["t"])}
                     for row in SalePayment.objects.filter(sale__in=sales).values("account__name").annotate(t=Sum("amount")).order_by("-t")],
        "cashiers": sorted(({**p, "total": _m(p["total"]), "discount": _m(p["discount"]), "returns": _m(p["returns"])} for p in cashiers.values()),
                           key=lambda p: -Decimal(p["total"])),
        "cash": {"registers": registers, "short": _m(short), "over": _m(over), "open": open_now, "stale": stale},
        "money": {"expenses": _m(expenses), "income": _m(income), "collected": _m(collected)},
        "stock": {"value": _m(value), "expired_batches": expired["n"] or 0, "expired_value": _m(expired["t"]),
                  "expiring_batches": expiring["n"] or 0, "expiring_value": _m(expiring["t"]), "movements": movements,
                  "write_offs": write_offs["n"] or 0, "write_off_value": _m(write_offs["t"]), "counts": counts, "count_loss": _m(count_loss),
                  "transit": transit["n"] or 0, "transit_value": _m(transit["t"])},
        "purchases": {"count": purchases["n"] or 0, "total": _m(purchases["t"]), "returns": _m(purchase_returns)},
        "books": {"sales": _m(pl["sales"]), "returns": _m(pl["returns"]), "net_sales": _m(pl["net_sales"]), "cogs": _m(pl["cogs"]),
                  "gross": _m(pl["gross"]), "margin": str(pl["gross_margin"] if pl["gross_margin"] is not None else ""),
                  "other_income": _m(pl["other_income_total"]), "expenses": _m(pl["expense_total"]), "net": _m(pl["net"]),
                  "doc_sales": _m(s["total"] - s["vat"]), "doc_returns": _m(r["total"]), "unposted": len(sale_ids) - posted,
                  "balanced": trial_balance(end)["balanced"]},
        "activity": {"deletions": deletions},
    }


def checklist(figures, shop):
    """[(key, flag, what the figures say)] for the checklist, in the order shown."""
    money = lambda v: format_money(v, shop)  # noqa: E731
    F_ = AuditCheck.Flag
    s, cash, stock, books = figures["sales"], figures["cash"], figures["stock"], figures["books"]
    rows = []

    short, over = Decimal(cash["short"]), Decimal(cash["over"])
    closed = sum(1 for reg in cash["registers"] if reg["closed"])
    rows.append(("cash", F_.WARN if short or over else F_.OK,
                 _("%(n)s registers closed: %(short)s short, %(over)s over.") % {"n": closed, "short": money(short), "over": money(over)}))
    rows.append(("open_registers", F_.WARN if cash["stale"] else F_.OK if not cash["open"] else F_.INFO,
                 _("%(open)s open now, %(stale)s of them for more than a day.") % {"open": cash["open"], "stale": cash["stale"]}))
    rows.append(("discounts", F_.WARN if Decimal(s["discount_pct"]) > DISCOUNT_LIMIT else F_.OK,
                 _("%(amount)s off, %(pct)s%% of sales before discounts (limit %(limit)s%%).") % {
                     "amount": money(s["discounts"]), "pct": s["discount_pct"], "limit": DISCOUNT_LIMIT}))
    rows.append(("returns", F_.WARN if Decimal(s["returns_pct"]) > RETURN_LIMIT else F_.OK,
                 _("%(n)s returns worth %(amount)s, %(pct)s%% of sales (limit %(limit)s%%).") % {
                     "n": s["returns_count"], "amount": money(s["returns"]), "pct": s["returns_pct"], "limit": RETURN_LIMIT}))
    rows.append(("below_cost", F_.WARN if s["below_cost"] else F_.OK,
                 _("%(n)s sale lines below cost, %(loss)s lost.") % {"n": s["below_cost"], "loss": money(s["below_cost_loss"])}))
    sales_match = Decimal(books["sales"]) == Decimal(books["doc_sales"]) and Decimal(books["returns"]) == Decimal(books["doc_returns"])
    rows.append(("books", F_.OK if sales_match else F_.WARN,
                 _("Bills %(docs)s, books %(books)s; returns %(rdocs)s, books %(rbooks)s.") % {
                     "docs": money(books["doc_sales"]), "books": money(books["sales"]), "rdocs": money(books["doc_returns"]),
                     "rbooks": money(books["returns"])}))
    rows.append(("posted", F_.WARN if books["unposted"] else F_.OK, _("%(n)s sales without a journal entry.") % {"n": books["unposted"]}))
    rows.append(("balance", F_.OK if books["balanced"] else F_.WARN,
                 _("Debits equal credits.") if books["balanced"] else _("Debits and credits don't match: rebuild the books.")))
    loss = Decimal(stock["write_off_value"]) + Decimal(stock["count_loss"])
    rows.append(("stock_loss", F_.WARN if _pct(loss, stock["value"]) > LOSS_LIMIT else F_.INFO if loss else F_.OK,
                 _("%(amount)s written off or missing in counts, against stock worth %(value)s.") % {
                     "amount": money(loss), "value": money(stock["value"])}))
    rows.append(("expired", F_.WARN if stock["expired_batches"] else F_.OK,
                 _("%(n)s expired batches still in stock (%(value)s); %(soon)s more expire within 30 days.") % {
                     "n": stock["expired_batches"], "value": money(stock["expired_value"]), "soon": stock["expiring_batches"]}))
    rows.append(("count", F_.OK if stock["counts"] else F_.WARN,
                 _("%(n)s stock counts in this period.") % {"n": len(stock["counts"])}))
    rows.append(("dues", F_.INFO, _("%(given)s given on due, %(collected)s collected.") % {
        "given": money(s["due"]), "collected": money(figures["money"]["collected"])}))
    rows.append(("transit", F_.INFO if stock["transit"] else F_.OK, _("%(n)s transfers to this branch not received yet (%(value)s).") % {
        "n": stock["transit"], "value": money(stock["transit_value"])}))
    rows.append(("activity", F_.INFO if figures["activity"]["deletions"] else F_.OK,
                 _("%(n)s records deleted in this period (all branches).") % {"n": figures["activity"]["deletions"]}))
    return rows


def _apply_checks(audit):
    shop = get_business_settings(audit.tenant)
    existing = {c.key: c for c in audit.checks.all()}
    for position, (key, flag, detail) in enumerate(checklist(audit.figures, shop)):
        check = existing.get(key) or AuditCheck(audit=audit, key=key)
        check.position, check.flag, check.detail = position, flag, detail[:300]
        check.save()


@transaction.atomic
def start_audit(*, tenant, outlet, start, end, user=None, note=""):
    if start > end:
        start, end = end, start
    audit = BranchAudit.objects.create(number=next_code(tenant, "branch_audit", prefix="AUD-", padding=4), outlet=outlet, start=start,
                                       end=end, note=note[:255], created_by=user,
                                       figures=branch_figures(outlet, start, end), figured_at=timezone.now())
    _apply_checks(audit)
    return audit


def _open(audit):
    if not audit.is_open:
        raise AuditError(_("%(number)s is signed off and can't change.") % {"number": audit.number})


@transaction.atomic
def refresh(audit):
    """Work the figures out again (late entries, corrections). The auditor's results and notes stay."""
    _open(audit)
    audit.figures, audit.figured_at = branch_figures(audit.outlet, audit.start, audit.end), timezone.now()
    audit.save(update_fields=["figures", "figured_at", "updated_at"])
    _apply_checks(audit)
    return audit


@transaction.atomic
def save_checks(audit, answers):
    """answers: {key: (result, note)} from the checklist form."""
    _open(audit)
    for check in audit.checks.all():
        if check.key not in answers:
            continue
        result, note = answers[check.key]
        check.result = result if result in AuditCheck.Result.values else ""
        check.note = (note or "").strip()[:300]
        check.save(update_fields=["result", "note", "updated_at"])


def add_finding(audit, *, title, severity, detail="", action="", user=None):
    _open(audit)
    if not (title or "").strip():
        raise AuditError(_("Give the finding a title."))
    return AuditFinding.objects.create(audit=audit, title=title.strip()[:150], severity=severity if severity in AuditFinding.Severity.values
                                       else AuditFinding.Severity.MEDIUM, detail=detail.strip(), action=action.strip()[:255], created_by=user)


@transaction.atomic
def sign_off(audit, *, verdict, summary, user=None):
    _open(audit)
    if verdict not in BranchAudit.Verdict.values:
        raise AuditError(_("Choose the verdict."))
    unanswered = [str(CHECK_TITLES.get(c.key, c.key)) for c in audit.checks.all() if not c.result]
    if unanswered:
        raise AuditError(_("Mark every check first: %(checks)s.") % {"checks": ", ".join(unanswered)})
    audit.status, audit.verdict, audit.summary = BranchAudit.Status.DONE, verdict, (summary or "").strip()
    audit.signed_by, audit.signed_at = user, timezone.now()
    audit.save(update_fields=["status", "verdict", "summary", "signed_by", "signed_at", "updated_at"])
    return audit


def reopen(audit):
    if audit.is_open:
        return audit
    audit.status, audit.signed_by, audit.signed_at = BranchAudit.Status.OPEN, None, None
    audit.save(update_fields=["status", "signed_by", "signed_at", "updated_at"])
    return audit


SEVERITY_ORDER = {AuditFinding.Severity.HIGH: 0, AuditFinding.Severity.MEDIUM: 1, AuditFinding.Severity.LOW: 2}


def ordered_findings(audit):
    return sorted(audit.findings.select_related("created_by"), key=lambda f: (f.resolved, SEVERITY_ORDER[f.severity], f.pk))
