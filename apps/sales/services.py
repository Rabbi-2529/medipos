"""Selling: cash registers, completing a sale (FEFO stock out), held carts and sale returns."""
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounting import posting
from apps.accounting.models import PaymentAccount
from apps.customers.models import CustomerLedgerEntry
from apps.customers.services import can_take_due
from apps.customers.services import post_entry as customer_entry
from apps.items.models import Medicine
from apps.messaging import loyalty
from apps.setup.services import get_business_settings, next_code
from apps.stock.models import BatchStock, StockMovement
from apps.stock.services import StockError, post_movement

from .models import (
    CashRegister, HeldCart, Promotion, Quotation, QuotationLine, Sale, SaleLine, SaleLineBatch, SalePayment, SaleReturn, SaleReturnLine,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")


class SaleError(Exception):
    """A user-facing reason a sale, return or register action was refused."""


def _money(value, what):
    try:
        number = Decimal(str(value if value not in (None, "") else 0))
    except InvalidOperation:
        raise SaleError(_("%(what)s must be a number.") % {"what": what})
    if number < 0:
        raise SaleError(_("%(what)s can't be negative.") % {"what": what})
    return number


# ---- Cash registers -----------------------------------------------------------------

def open_register(user, outlet):
    if outlet is None:
        return None
    return CashRegister.objects.filter(outlet=outlet, opened_by=user, closed_at__isnull=True).first()


@transaction.atomic
def start_register(user, outlet, opening_cash, *, counter=None, note=""):
    if outlet is None:
        raise SaleError(_("You don't have an outlet to sell in."))
    if open_register(user, outlet):
        raise SaleError(_("Your register at %(outlet)s is already open.") % {"outlet": outlet.name})
    return CashRegister.objects.create(outlet=outlet, counter=counter, opened_by=user,
                                       opening_cash=_money(opening_cash, _("Opening cash")), note=note)


def register_summary(register):
    """Totals for the Z-report: sales, discounts, money in/out per account, and the cash that should be in the drawer."""
    sales = register.sales.all()
    totals = sales.aggregate(total=Sum("total"), paid=Sum("paid"),
                             discount=Sum("discount"), line_discount=Sum("line_discount"), vat=Sum("vat_amount"))
    by_account = defaultdict(lambda: ZERO)
    for row in SalePayment.objects.filter(sale__register=register).values("account__name", "account__kind").annotate(t=Sum("amount")):
        by_account[(row["account__name"], row["account__kind"])] += row["t"]
    for row in register.receipts.values("account__name", "account__kind").annotate(t=Sum("amount")):
        by_account[(row["account__name"], row["account__kind"])] += row["t"]
    for row in register.returns.exclude(refund_account=None).values("refund_account__name", "refund_account__kind").annotate(t=Sum("refund_amount")):
        by_account[(row["refund_account__name"], row["refund_account__kind"])] -= row["t"]
    # Expenses paid from the drawer, other income put in it, and money moved in or out by the owner.
    paid_out = ZERO
    for row in register.cash_entries.values("payment_account__name", "payment_account__kind", "kind").annotate(t=Sum("amount")):
        sign = 1 if row["kind"] == "income" else -1
        by_account[(row["payment_account__name"], row["payment_account__kind"])] += sign * row["t"]
        if sign < 0:
            paid_out += row["t"]
    for movement in register.fund_movements.select_related("from_account", "to_account"):
        if movement.to_account:
            by_account[(movement.to_account.name, movement.to_account.kind)] += movement.amount
        if movement.from_account:
            by_account[(movement.from_account.name, movement.from_account.kind)] -= movement.amount
    cash_in = sum((amount for (name, kind), amount in by_account.items() if kind == PaymentAccount.Kind.CASH), ZERO)
    return {
        "sale_count": sales.count(),
        "sales_total": totals["total"] or ZERO,
        "collected_at_sale": totals["paid"] or ZERO,
        "discounts": (totals["discount"] or ZERO) + (totals["line_discount"] or ZERO),
        "vat": totals["vat"] or ZERO,
        "dues_given": (totals["total"] or ZERO) - (totals["paid"] or ZERO),
        "returns": register.returns.aggregate(t=Sum("total"))["t"] or ZERO,
        "expenses": paid_out,
        "by_account": sorted(((name, amount) for (name, _kind), amount in by_account.items()), key=lambda x: x[0]),
        "expected_cash": register.opening_cash + cash_in,
    }


@transaction.atomic
def close_register(register, counted_cash, *, note=""):
    register = CashRegister.objects.select_for_update().get(pk=register.pk)
    if not register.is_open:
        raise SaleError(_("This register is already closed."))
    register.expected_cash = register_summary(register)["expected_cash"]
    register.counted_cash = _money(counted_cash, _("Counted cash"))
    register.closed_at = timezone.now()
    register.note = note or register.note
    register.save(update_fields=["expected_cash", "counted_cash", "closed_at", "note", "updated_at"])
    return register


# ---- Units & prices -----------------------------------------------------------------

def unit_options(medicine):
    """What a medicine can be sold by: [(key, label, sale units)]."""
    options = [("piece", medicine.sale_unit.name, 1)]
    if medicine.strip_size and medicine.strip_size > 1:
        options.append(("strip", _("Strip"), medicine.strip_size))
    if medicine.conversion > 1:
        options.append(("pack", medicine.purchase_unit.name, medicine.conversion))
    return options


def unit_price(medicine, price_type):
    if price_type == Sale.PriceType.WHOLESALE and medicine.wholesale_price is not None:
        return medicine.wholesale_price
    return medicine.sale_price


def sellable_stock(outlet, medicine, today=None):
    """Units that may be sold now (expired batches don't count)."""
    today = today or timezone.localdate()
    return (BatchStock.objects.filter(outlet=outlet, medicine=medicine, quantity__gt=0)
            .exclude(batch__expiry_date__lte=today).aggregate(q=Sum("quantity"))["q"] or 0)


def allocate(outlet, medicine, quantity, *, batch_id=None, today=None):
    """[(batch, units)] to take, earliest expiry first. Expired batches are never sold."""
    today = today or timezone.localdate()
    stocks = (BatchStock.objects.filter(outlet=outlet, medicine=medicine, quantity__gt=0)
              .exclude(batch__expiry_date__lte=today).select_related("batch").order_by("batch__expiry_date", "batch__id"))
    if batch_id:
        stocks = stocks.filter(batch_id=batch_id)
    picks, needed = [], quantity
    for stock in stocks:
        take = min(stock.quantity, needed)
        picks.append((stock.batch, take))
        needed -= take
        if needed == 0:
            return picks
    have = quantity - needed
    if batch_id:
        raise SaleError(_("%(name)s: the chosen batch has only %(have)s left (or has expired).") % {"name": medicine.display_name, "have": have})
    raise SaleError(_("%(name)s: only %(have)s in stock that hasn't expired, %(want)s needed.") % {
        "name": medicine.display_name, "have": have, "want": quantity})


def check_stock(outlet, cart, today=None):
    """Fail early (before payments are looked at) when the cart asks for more sellable stock than there is."""
    today = today or timezone.localdate()
    per_medicine, per_batch = defaultdict(int), defaultdict(int)
    for line in cart:
        per_medicine[line["medicine"]] += line["quantity"]
        if line["batch_id"]:
            per_batch[(line["medicine"], line["batch_id"])] += line["quantity"]
    for medicine, needed in per_medicine.items():
        have = sellable_stock(outlet, medicine, today)
        if needed > have:
            raise SaleError(_("%(name)s: only %(have)s in stock that hasn't expired, %(want)s needed.") % {
                "name": medicine.display_name, "have": have, "want": needed})
    for (medicine, batch_id), needed in per_batch.items():
        have = (BatchStock.objects.filter(outlet=outlet, batch_id=batch_id, quantity__gt=0)
                .exclude(batch__expiry_date__lte=today).values_list("quantity", flat=True).first() or 0)
        if needed > have:
            raise SaleError(_("%(name)s: the chosen batch has only %(have)s left (or has expired).") % {
                "name": medicine.display_name, "have": have})


# ---- Promotions ----------------------------------------------------------------------

def running_promotions(day=None):
    """Promotions on today, each with the ids of its chosen medicines loaded once."""
    day = day or timezone.localdate()
    promotions = [p for p in Promotion.objects.filter(is_active=True).prefetch_related("medicines") if p.runs_on(day)]
    for promotion in promotions:
        promotion.medicine_ids = {m.pk for m in promotion.medicines.all()}
    return promotions


def promotion_applies(promotion, medicine):
    scope = promotion.scope
    return (scope == Promotion.Scope.ALL
            or (scope == Promotion.Scope.CATEGORY and medicine.category_id == promotion.category_id)
            or (scope == Promotion.Scope.FORM and medicine.dosage_form == promotion.dosage_form)
            or (scope == Promotion.Scope.MEDICINES and medicine.pk in promotion.medicine_ids))


def best_promotion(medicine, promotions, price):
    """The promotion saving the most per unit on this medicine (at its minimum quantity), or None."""
    best, best_saving = None, ZERO
    for promotion in promotions:
        if not promotion_applies(promotion, medicine):
            continue
        units = max(promotion.min_quantity, 1)
        saving = promotion.discount(price, units) / units
        if saving > best_saving:
            best, best_saving = promotion, saving
    return best


def clean_cart(raw_lines, price_type, promotions=None):
    """Check and price the POS lines. A line may name the promotion the POS applied; it is kept only if it really applies."""
    if not raw_lines:
        raise SaleError(_("The cart is empty."))
    promotions = {p.pk: p for p in (running_promotions() if promotions is None else promotions)}
    ids = {str(line.get("medicine")) for line in raw_lines}
    medicines = {str(m.pk): m for m in Medicine.objects.filter(pk__in=[i for i in ids if i.isdigit()], is_active=True)
                 .select_related("sale_unit", "purchase_unit")}
    lines = []
    for raw in raw_lines:
        medicine = medicines.get(str(raw.get("medicine")))
        if medicine is None:
            raise SaleError(_("A medicine in the cart is no longer available."))
        options = {key: (label, factor) for key, label, factor in unit_options(medicine)}
        unit = raw.get("unit") or "piece"
        if unit not in options:
            raise SaleError(_("%(name)s can't be sold by that unit.") % {"name": medicine.display_name})
        try:
            unit_qty = int(raw.get("qty") or 0)
        except (TypeError, ValueError):
            unit_qty = 0
        if unit_qty < 1:
            raise SaleError(_("%(name)s: quantity must be at least 1.") % {"name": medicine.display_name})
        label, factor = options[unit]
        price = unit_price(medicine, price_type)
        quantity = unit_qty * factor
        gross = (price * quantity).quantize(CENT, ROUND_HALF_UP)
        discount = _money(raw.get("discount"), _("Discount")).quantize(CENT)
        if discount > gross:
            raise SaleError(_("%(name)s: the discount is more than the price.") % {"name": medicine.display_name})
        batch_id = raw.get("batch")
        promotion = promotions.get(int(raw["promotion"])) if str(raw.get("promotion") or "").isdigit() else None
        if promotion is not None and not promotion_applies(promotion, medicine):
            promotion = None
        lines.append({
            "medicine": medicine, "unit_label": str(label), "unit_factor": factor, "unit_qty": unit_qty, "quantity": quantity,
            "unit_price": price, "gross": gross, "discount": discount, "line_total": gross - discount,
            "batch_id": int(batch_id) if str(batch_id or "").isdigit() else None, "promotion": promotion,
        })
    return lines


def totals(lines, *, cart_discount, settings):
    subtotal = sum((l["gross"] for l in lines), ZERO)
    line_discount = sum((l["discount"] for l in lines), ZERO)
    after_lines = subtotal - line_discount
    cart_discount = _money(cart_discount, _("Discount")).quantize(CENT)
    if cart_discount > after_lines:
        raise SaleError(_("The discount is more than the bill."))
    taxable = after_lines - cart_discount
    vat = (taxable * settings.vat_percent / 100).quantize(CENT, ROUND_HALF_UP)
    exact = taxable + vat
    total = exact.quantize(Decimal("1"), ROUND_HALF_UP) if settings.round_total else exact
    return {"subtotal": subtotal, "line_discount": line_discount, "discount": cart_discount, "vat_percent": settings.vat_percent,
            "vat_amount": vat, "rounding": total - exact, "total": total}


# ---- Completing a sale -----------------------------------------------------------------

@transaction.atomic
def complete_sale(*, tenant, outlet, user, lines, payments, register=None, customer=None, price_type=Sale.PriceType.RETAIL,
                  cart_discount=0, cash_tendered=None, rx_checked=False, note="", now=None, quotation_id=None, points=0, offline_ref=""):
    """Validate the cart, take stock (FEFO), record payments and the customer's due. All or nothing.
    `points`: loyalty points the customer spends; their value comes off the bill like a discount."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    if price_type not in Sale.PriceType.values:
        price_type = Sale.PriceType.RETAIL
    settings = get_business_settings(tenant)
    cart = clean_cart(lines, price_type)
    check_stock(outlet, cart, today)
    if any(l["medicine"].prescription_required for l in cart) and not rx_checked:
        raise SaleError(_("This sale has prescription medicines. Check the prescription and tick the box."))
    amounts = totals(cart, cart_discount=cart_discount, settings=settings)
    points = int(points) if str(points or "").isdigit() else 0
    points_discount = ZERO
    if points:
        try:
            points_discount = loyalty.redeem_value(customer, points, amounts["total"], loyalty.program())
        except loyalty.LoyaltyError as error:
            raise SaleError(str(error))
        amounts = totals(cart, cart_discount=_money(cart_discount, _("Discount")) + points_discount, settings=settings)

    accounts = {a.pk: a for a in PaymentAccount.objects.filter(is_active=True)}
    clean_payments = []
    for payment in payments or []:
        amount = _money(payment.get("amount"), _("Payment")).quantize(CENT)
        if amount == 0:
            continue
        account = accounts.get(int(payment.get("account") or 0)) if str(payment.get("account") or "").isdigit() else None
        if account is None:
            raise SaleError(_("Choose where each payment goes."))
        clean_payments.append((account, amount, str(payment.get("reference") or "")[:100]))
    paid = sum((amount for _a, amount, _r in clean_payments), ZERO)
    if paid > amounts["total"]:
        raise SaleError(_("Payments are more than the bill. Enter the cash tendered to work out the change instead."))
    due = amounts["total"] - paid
    if due > 0:
        if customer is None:
            raise SaleError(_("Pick a customer to give the remaining %(due)s on due.") % {"due": due})
        problem = can_take_due(customer, due)
        if problem:
            raise SaleError(problem)

    number = next_code(tenant, "invoice", prefix="INV", padding=5)
    sale = Sale.objects.create(
        number=number, outlet=outlet, register=register, customer=customer, sold_at=now, price_type=price_type,
        paid=paid, cash_tendered=_money(cash_tendered, _("Cash tendered")) if cash_tendered not in (None, "") else None,
        rx_checked=rx_checked, note=note[:255], created_by=user, points_redeemed=points, points_discount=points_discount,
        offline_ref=offline_ref[:40], **amounts,
    )
    ref = ("sale", sale.pk, number)
    cost_total = ZERO
    for item in cart:
        line = SaleLine.objects.create(
            sale=sale, medicine=item["medicine"], unit_label=item["unit_label"], unit_factor=item["unit_factor"],
            unit_qty=item["unit_qty"], quantity=item["quantity"], unit_price=item["unit_price"],
            discount=item["discount"], line_total=item["line_total"], promotion=item["promotion"],
        )
        line_cost = ZERO
        for batch, take in allocate(outlet, item["medicine"], item["quantity"], batch_id=item["batch_id"], today=today):
            SaleLineBatch.objects.create(line=line, batch=batch, quantity=take, unit_cost=batch.unit_cost)
            try:
                post_movement(outlet=outlet, batch=batch, quantity=-take, kind=StockMovement.Kind.SALE, unit_cost=batch.unit_cost,
                              ref=ref, user=user)
            except StockError as error:
                raise SaleError(str(error))
            line_cost += batch.unit_cost * take
        line.cost_total = line_cost
        line.save(update_fields=["cost_total", "updated_at"])
        cost_total += line_cost
    sale.cost_total = cost_total
    sale.save(update_fields=["cost_total", "updated_at"])
    for account, amount, reference in clean_payments:
        SalePayment.objects.create(sale=sale, account=account, amount=amount, reference=reference)
    if customer is not None:
        customer_entry(customer, date=today, kind=CustomerLedgerEntry.Kind.SALE, debit=sale.total, ref=ref)
        if paid > 0:
            customer_entry(customer, date=today, kind=CustomerLedgerEntry.Kind.PAYMENT, credit=paid, ref=ref)
        loyalty.record_sale(sale, user=user)
    if quotation_id:
        Quotation.objects.filter(pk=quotation_id, status=Quotation.Status.OPEN).update(status=Quotation.Status.SOLD, sale=sale)
    posting.post_sale(sale)
    return sale


# ---- Quotations ----------------------------------------------------------------------

@transaction.atomic
def create_quotation(*, tenant, outlet, user, lines, customer=None, price_type=Sale.PriceType.RETAIL, cart_discount=0,
                     valid_days=7, note=""):
    """Price the POS cart as a quote. Nothing leaves stock; it can be sold later from the POS."""
    if price_type not in Sale.PriceType.values:
        price_type = Sale.PriceType.RETAIL
    items = clean_cart(lines, price_type)
    amounts = totals(items, cart_discount=cart_discount, settings=get_business_settings(tenant))
    today = timezone.localdate()
    valid_days = min(max(int(valid_days or 7), 1), 365)
    quotation = Quotation.objects.create(
        number=next_code(tenant, "quotation", prefix="QT-", padding=5), outlet=outlet, customer=customer, date=today,
        valid_until=today + timedelta(days=valid_days), price_type=price_type, note=note[:255], created_by=user,
        **amounts,
    )
    for item in items:
        QuotationLine.objects.create(
            quotation=quotation, medicine=item["medicine"], unit_label=item["unit_label"], unit_factor=item["unit_factor"],
            unit_qty=item["unit_qty"], quantity=item["quantity"], unit_price=item["unit_price"],
            discount=item["discount"], line_total=item["line_total"],
        )
    return quotation


# ---- Held carts ----------------------------------------------------------------------

def hold_cart(*, outlet, user, data, label=""):
    if not (data or {}).get("lines"):
        raise SaleError(_("The cart is empty."))
    return HeldCart.objects.create(outlet=outlet, held_by=user, data=data, label=label[:80])


def recall_cart(outlet, pk):
    held = HeldCart.objects.filter(outlet=outlet, pk=pk).first()
    if held is None:
        raise SaleError(_("That held cart is gone (someone may have recalled it)."))
    data = held.data
    held.delete()
    return data


# ---- Returns -------------------------------------------------------------------------

def returnable(sale):
    returned = dict(SaleReturnLine.objects.filter(sale_line__sale=sale).values("sale_line").annotate(q=Sum("quantity"))
                    .values_list("sale_line", "q"))
    return [(line, line.quantity - (returned.get(line.pk) or 0)) for line in sale.lines.select_related("medicine__sale_unit")]


def refund_factor(sale):
    """Share of each line's own total the customer actually paid (cart discount, VAT and rounding spread evenly)."""
    base = sale.subtotal - sale.line_discount
    return (sale.total / base) if base else Decimal("1")


@transaction.atomic
def create_return(sale, quantities, *, date, refund_amount=0, refund_account=None, reason="", register=None, user=None):
    sale = Sale.objects.select_for_update().get(pk=sale.pk)
    available = {line.pk: (line, left) for line, left in returnable(sale)}
    picked = [(available[pk], qty) for pk, qty in quantities.items() if qty and pk in available]
    if not picked:
        raise SaleError(_("Enter how many units come back."))
    factor = refund_factor(sale)
    number = next_code(sale.tenant, "sale_return", prefix="SRT-", padding=5)
    sale_return = SaleReturn.objects.create(number=number, sale=sale, outlet=sale.outlet, customer=sale.customer,
                                            register=register, date=date, reason=reason[:255], created_by=user)
    ref = ("sale_return", sale_return.pk, number)
    returned_by_batch = defaultdict(int)
    for row in SaleReturnLine.objects.filter(sale_line__sale=sale).values("sale_line", "batch").annotate(q=Sum("quantity")):
        returned_by_batch[(row["sale_line"], row["batch"])] = row["q"]
    total = ZERO
    for (line, left), qty in picked:
        if qty < 0 or qty > left:
            raise SaleError(_("%(name)s: at most %(left)s can come back.") % {"name": line.medicine.display_name, "left": left})
        price = (line.net_unit_price * factor).quantize(Decimal("0.0001"))
        need = qty
        for allocation in line.allocations.select_related("batch").order_by("-id"):
            room = allocation.quantity - returned_by_batch[(line.pk, allocation.batch_id)]
            back = min(room, need)
            if back <= 0:
                continue
            amount = (price * back).quantize(CENT, ROUND_HALF_UP)
            SaleReturnLine.objects.create(sale_return=sale_return, sale_line=line, batch=allocation.batch, quantity=back,
                                          unit_price=price, line_total=amount)
            post_movement(outlet=sale.outlet, batch=allocation.batch, quantity=back, kind=StockMovement.Kind.SALE_RETURN,
                          unit_cost=allocation.unit_cost, ref=ref, user=user, note=reason)
            total += amount
            need -= back
            if need == 0:
                break
    refund = _money(refund_amount, _("Refund")).quantize(CENT)
    if refund > total:
        raise SaleError(_("The refund can't be more than the returned value (%(total)s).") % {"total": total})
    if sale.customer is None and refund != total:
        raise SaleError(_("This sale has no customer, so the full %(total)s must be refunded.") % {"total": total})
    if refund > 0 and refund_account is None:
        raise SaleError(_("Choose where the refund is paid from."))
    sale_return.total, sale_return.refund_amount = total, refund
    sale_return.refund_account = refund_account if refund > 0 else None
    sale_return.save(update_fields=["total", "refund_amount", "refund_account", "updated_at"])
    if sale.customer is not None:
        customer_entry(sale.customer, date=date, kind=CustomerLedgerEntry.Kind.RETURN, credit=total, ref=ref)
        if refund > 0:
            customer_entry(sale.customer, date=date, kind=CustomerLedgerEntry.Kind.REFUND, debit=refund, ref=ref)
        loyalty.on_return(sale_return, user=user)
    posting.post_sale_return(sale_return)
    return sale_return


def batch_choices(outlet, medicine, today=None):
    """Sellable batches for the POS's "choose batch" list, earliest expiry first."""
    today = today or timezone.localdate()
    rows = (BatchStock.objects.filter(outlet=outlet, medicine=medicine, quantity__gt=0).select_related("batch")
            .order_by("batch__expiry_date", "batch__id"))
    return [{"id": s.batch_id, "batch_no": s.batch.batch_no or "—", "expiry": s.batch.expiry_date.isoformat() if s.batch.expiry_date else "",
             "quantity": s.quantity, "expired": bool(s.batch.expiry_date and s.batch.expiry_date <= today)} for s in rows]
