import json
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Case, Count, DateField, F, IntegerField, Min, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from apps.accounting.models import PaymentAccount
from apps.core.datatables import datatable_json
from apps.core.shop_views import ShopCreateView, ShopUpdateView
from apps.customers.models import Customer
from apps.customers.services import sale_due
from apps.items.choices import DosageForm
from apps.items.models import Category, Medicine
from apps.items.services import search_medicines
from apps.outlets.models import Counter
from apps.people.access import has_perm, shop_permission_required
from apps.setup.services import format_money, format_shop_date, get_business_settings
from apps.stock.models import BatchStock

from .forms import PromotionForm
from .models import CashRegister, HeldCart, Promotion, Quotation, Sale, SaleLine, SaleReturn
from .services import (
    SaleError, batch_choices, best_promotion, close_register, complete_sale, create_quotation, create_return, hold_cart,
    open_register, recall_cart, refund_factor, register_summary, returnable, running_promotions, start_register, unit_options,
)


def _json_body(request):
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _sales(request):
    return Sale.objects.filter(outlet_id__in=[o.pk for o in request.allowed_outlets])


# ---- POS --------------------------------------------------------------------------

@shop_permission_required("pos.create")
def pos(request):
    if request.outlet is None:
        messages.error(request, _("You haven't been given an outlet to sell in."))
        return redirect("core:dashboard")
    register = open_register(request.user, request.outlet)
    if register is None:
        return render(request, "sales/register_open.html", {
            "counters": Counter.objects.filter(outlet=request.outlet, is_active=True),
        })
    settings = get_business_settings(request.tenant)
    accounts = list(PaymentAccount.objects.filter(is_active=True).values("id", "name", "kind"))
    quotation = None
    if str(request.GET.get("quotation", "")).isdigit() and has_perm(request.user, "quotation.list"):
        found = Quotation.objects.filter(pk=request.GET["quotation"], status=Quotation.Status.OPEN).first()
        if found is not None:
            quotation = {"id": found.pk, "number": found.number, "cart": _quotation_cart(found, request.outlet)}
    return render(request, "sales/pos.html", {
        "register": register,
        "config": {
            "currency": settings.currency_symbol, "decimals": settings.decimal_places,
            "vatPercent": str(settings.vat_percent), "roundTotal": settings.round_total,
            "accounts": accounts, "receiptSize": settings.receipt_size,
            "urls": {
                "search": reverse("sales:pos_search"), "batches": reverse("sales:pos_batches"), "checkout": reverse("sales:pos_checkout"),
                "hold": reverse("sales:pos_hold"), "held": reverse("sales:pos_held"), "recall": reverse("sales:pos_recall"),
                "customers": reverse("customers:lookup"), "newCustomer": reverse("customers:quick_create"),
                "quote": reverse("sales:pos_quote"),
            },
            "canAddCustomer": has_perm(request.user, "customer.create"),
            "canQuote": has_perm(request.user, "quotation.create"),
            "quotation": quotation,
        },
        "shelves": pos_shelves(),
        "texts": pos_texts(),
    })


# Colour ("tone", a CSS class in app.css) and icon for each dosage form on the POS cards and buttons.
FORM_STYLES = {
    "tablet": ("violet", "bi-capsule-pill"), "capsule": ("teal", "bi-capsule"), "suppository": ("teal", "bi-capsule"),
    "syrup": ("orange", "bi-droplet-half"), "suspension": ("orange", "bi-droplet-half"), "solution": ("orange", "bi-droplet-half"),
    "drops": ("sky", "bi-eyedropper"), "mouthwash": ("sky", "bi-cup-straw"),
    "injection": ("red", "bi-eyedropper"), "infusion": ("red", "bi-eyedropper"),
    "cream": ("pink", "bi-bandaid"), "ointment": ("pink", "bi-bandaid"), "gel": ("pink", "bi-bandaid"),
    "lotion": ("pink", "bi-bandaid"), "shampoo": ("pink", "bi-droplet"),
    "spray": ("cyan", "bi-wind"), "inhaler": ("cyan", "bi-wind"),
    "powder": ("amber", "bi-box2"), "sachet": ("amber", "bi-box2"), "granules": ("amber", "bi-box2"),
}
OTHER_STYLE = ("slate", "bi-prescription2")


def pos_shelves():
    """Quick-pick buttons for the POS: dosage forms in use (most medicines first), then the pharmacy's categories.

    A lone category holding every medicine would just repeat "All medicines", so it is left out.
    """
    medicines = Medicine.objects.filter(is_active=True)
    labels = dict(DosageForm.choices)
    forms = [{"key": f"form:{row['dosage_form']}", "name": labels.get(row["dosage_form"], row["dosage_form"]),
              "tone": FORM_STYLES.get(row["dosage_form"], OTHER_STYLE)[0], "icon": FORM_STYLES.get(row["dosage_form"], OTHER_STYLE)[1]}
             for row in medicines.exclude(dosage_form="").values("dosage_form").annotate(n=Count("id")).order_by("-n", "dosage_form")]
    categories = list(Category.objects.filter(is_active=True, medicines__is_active=True).distinct().order_by("name"))
    if len(categories) == 1 and not medicines.filter(category__isnull=True).exists():
        categories = []
    return forms + [{"key": str(c.pk), "name": c.name, "tone": "indigo", "icon": "bi-tag"} for c in categories]


def _quotation_cart(quotation, outlet):
    """A quotation as a POS cart, priced and stocked as of now. Quoted line discounts are kept as the cashier's own."""
    from apps.customers.views import _lookup_row

    lines = list(quotation.lines.all())
    medicines = _pos_medicines(outlet).in_bulk([line.medicine_id for line in lines])
    promotions = running_promotions()
    cart_lines = []
    for line in lines:
        medicine = medicines.get(line.medicine_id)
        if medicine is None:
            continue
        row = _pos_row(medicine, promotions)
        unit = next((u for u in row["units"] if u["label"] == line.unit_label), None) or row["units"][0]
        qty = line.unit_qty if unit["label"] == line.unit_label else line.quantity
        cart_lines.append({
            "medicine": medicine.pk, "name": row["name"], "rx": row["rx"], "price": row["price"], "wholesale": row["wholesale"],
            "units": row["units"], "stock": row["stock"], "unit": unit["key"], "qty": qty, "batch": None, "batchLabel": "",
            "promo": row["promo"], "discount": str(line.discount) if line.discount else "", "autoDiscount": False if line.discount else None,
        })
    return {
        "lines": cart_lines, "priceType": quotation.price_type, "discount": str(quotation.discount) if quotation.discount else "",
        "discountMode": "amount", "customer": {**_lookup_row(quotation.customer), "text": str(quotation.customer)} if quotation.customer else None,
    }


def pos_texts():
    """Words the POS script shows, translated here so the page speaks the user's language."""
    return {
        "walkIn": _("Walk-in customer (search name or phone)"), "owes": _("Owes"), "limit": _("limit"),
        "noLimit": _("no credit limit"), "noCredit": _("no credit allowed"), "discountPct": _("default discount"),
        "inStock": _("in stock"), "outOfStock": _("Out of stock"), "expires": _("exp."),
        "noResults": _("No medicine matches. Check the spelling or scan again."), "onlyInStock": _("Only %(n)s in stock."),
        "auto": _("Automatic (earliest expiry first)"), "batch": _("Batch"), "chooseBatch": _("choose batch"),
        "expired": _("Expired"), "remove": _("Remove"), "cartEmpty": _("The cart is empty."), "held": _("Cart held."),
        "noHeld": _("No held carts."), "items": _("items"), "clearConfirm": _("Clear the whole cart?"),
        "dueNeedsCustomer": _("Pick a customer to leave %(due)s on due."),
        "dueNote": _("%(due)s will be added to the customer’s due."), "change": _("Change"), "due": _("Due"),
        "account": _("Account"), "amount": _("Amount"), "error": _("Something went wrong. Please try again."),
        "qty": _("Qty"), "lineDiscount": _("Discount"), "less": _("One less"), "more": _("One more"),
        "roundedFrom": _("%(exact)s rounded to %(total)s"),
        "emptyCategory": _("No medicines here yet."), "noSalesYet": _("No sales yet. These are the medicines you have most of."),
        "showingFirst": _("Showing the first %(n)s. Search to find the rest."),
        "promo": _("Offer"), "quoteSaved": _("Quotation %(number)s saved."), "fromQuote": _("Loaded from quotation %(number)s"),
    }


BROWSE_LIMIT = 48       # medicine cards shown before the cashier types anything
TOP_SELLER_DAYS = 90    # "Best sellers" looks at this many days of sales in the outlet


def _pos_medicines(outlet):
    """Active medicines with the outlet's sellable stock (not expired) and nearest expiry."""
    today = timezone.localdate()
    sellable = (BatchStock.objects.filter(outlet=outlet, medicine=OuterRef("pk"), quantity__gt=0)
                .exclude(batch__expiry_date__lte=today).order_by().values("medicine"))
    return Medicine.objects.filter(is_active=True).select_related("generic", "manufacturer", "sale_unit", "purchase_unit").annotate(
        stock=Coalesce(Subquery(sellable.annotate(q=Sum("quantity")).values("q"), output_field=IntegerField()), Value(0)),
        expiry=Subquery(sellable.annotate(e=Min("batch__expiry_date")).values("e"), output_field=DateField()),
    )


def _pos_row(m, promotions=()):
    tone, icon = FORM_STYLES.get(m.dosage_form, OTHER_STYLE)
    promotion = best_promotion(m, promotions, m.sale_price) if promotions else None
    return {
        "promo": {"id": promotion.pk, "name": promotion.name, "kind": promotion.kind, "value": str(promotion.value),
                  "minQty": promotion.min_quantity} if promotion else None,
        "id": m.pk, "tone": tone, "icon": icon, "name": m.display_name, "generic": m.generic.name if m.generic else "",
        "maker": m.manufacturer.name if m.manufacturer else "", "code": m.code, "rx": m.prescription_required,
        "price": str(m.sale_price), "wholesale": str(m.wholesale_price) if m.wholesale_price is not None else None,
        "stock": m.stock, "nearestExpiry": m.expiry.isoformat() if m.expiry else "",
        "units": [{"key": k, "label": str(label), "factor": f} for k, label, f in unit_options(m)],
    }


def _browse(outlet, queryset, category):
    """What the POS shows before a search: best sellers, a dosage form ("form:tablet"), a category id, or "all" (in-stock first)."""
    in_stock_first = queryset.annotate(has_stock=Case(When(stock__gt=0, then=Value(1)), default=Value(0), output_field=IntegerField()))
    if category == "top":
        since = timezone.now() - timedelta(days=TOP_SELLER_DAYS)
        ranked = [row["medicine"] for row in SaleLine.objects.filter(sale__outlet=outlet, sale__sold_at__gte=since)
                  .values("medicine").annotate(units=Sum("quantity")).order_by("-units")[:BROWSE_LIMIT]]
        found = queryset.in_bulk(ranked)
        rows = [found[pk] for pk in ranked if pk in found]
        # Fill the rest with what's on the shelf, so a new shop doesn't start with an empty screen.
        rows += list(queryset.filter(stock__gt=0).exclude(pk__in=ranked).order_by("-stock", "name")[:BROWSE_LIMIT - len(rows)])
        return rows, bool(found), False
    if category.startswith("form:"):
        in_stock_first = in_stock_first.filter(dosage_form=category.removeprefix("form:"))
    elif category.isdigit():
        in_stock_first = in_stock_first.filter(category_id=category)
    elif category != "all":
        return [], False, False
    rows = list(in_stock_first.order_by("-has_stock", "name")[:BROWSE_LIMIT + 1])
    return rows[:BROWSE_LIMIT], False, len(rows) > BROWSE_LIMIT


@shop_permission_required("pos.create")
def pos_search(request):
    """Typed text searches; with nothing typed, ?category=top|all|<id> browses (see _browse)."""
    term = request.GET.get("q", "").strip()
    queryset = _pos_medicines(request.outlet)
    promotions = running_promotions()
    if not term:
        rows, has_sales, more = _browse(request.outlet, queryset, request.GET.get("category", "top"))
        return JsonResponse({"exact": False, "results": [_pos_row(m, promotions) for m in rows], "hasSales": has_sales, "more": more})
    exact = list(queryset.filter(Q(barcode=term) | Q(code__iexact=term))[:1])
    rows = exact or list(search_medicines(queryset, term).order_by("-stock", "name")[:20])
    return JsonResponse({"exact": bool(exact), "results": [_pos_row(m, promotions) for m in rows]})


@shop_permission_required("pos.create")
def pos_batches(request):
    medicine = get_object_or_404(Medicine.objects.all(), pk=request.GET.get("medicine") if str(request.GET.get("medicine", "")).isdigit() else 0)
    return JsonResponse({"results": batch_choices(request.outlet, medicine)})


@shop_permission_required("pos.create")
@require_POST
def pos_checkout(request):
    register = open_register(request.user, request.outlet)
    if register is None:
        return JsonResponse({"error": _("Open your cash register first.")}, status=400)
    data = _json_body(request)
    customer = None
    if str(data.get("customer") or "").isdigit():
        customer = Customer.objects.filter(pk=data["customer"], is_active=True).first()
    try:
        sale = complete_sale(
            tenant=request.tenant, outlet=request.outlet, register=register, user=request.user, lines=data.get("lines") or [],
            payments=data.get("payments") or [], customer=customer, price_type=data.get("priceType") or Sale.PriceType.RETAIL,
            cart_discount=data.get("discount") or 0, cash_tendered=data.get("cashTendered"), rx_checked=bool(data.get("rxChecked")),
            note=str(data.get("note") or ""), quotation_id=int(data["quotation"]) if str(data.get("quotation") or "").isdigit() else None,
        )
    except SaleError as error:
        return JsonResponse({"error": str(error)}, status=400)
    settings = get_business_settings(request.tenant)
    held_id = data.get("heldId")
    if str(held_id or "").isdigit():
        HeldCart.objects.filter(pk=held_id, outlet=request.outlet).delete()
    return JsonResponse({
        "id": sale.pk, "number": sale.number, "total": format_money(sale.total, settings), "paid": format_money(sale.paid, settings),
        "due": format_money(sale.due, settings) if sale.due > 0 else "", "change": format_money(sale.change, settings) if sale.change > 0 else "",
        "receiptUrl": reverse("sales:receipt", args=[sale.pk]),
    })


@shop_permission_required("quotation.create")
@require_POST
def pos_quote(request):
    """Save the POS cart as a quotation (nothing leaves stock)."""
    data = _json_body(request)
    customer = None
    if str(data.get("customer") or "").isdigit():
        customer = Customer.objects.filter(pk=data["customer"], is_active=True).first()
    try:
        quotation = create_quotation(
            tenant=request.tenant, outlet=request.outlet, user=request.user, lines=data.get("lines") or [], customer=customer,
            price_type=data.get("priceType") or Sale.PriceType.RETAIL, cart_discount=data.get("discount") or 0,
            valid_days=data.get("validDays") if str(data.get("validDays") or "").isdigit() else 7, note=str(data.get("note") or ""),
        )
    except SaleError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return JsonResponse({"id": quotation.pk, "number": quotation.number, "printUrl": reverse("sales:quotation_print", args=[quotation.pk]),
                         "url": reverse("sales:quotation", args=[quotation.pk])})


@shop_permission_required("pos.create")
@require_POST
def pos_hold(request):
    data = _json_body(request)
    try:
        held = hold_cart(outlet=request.outlet, user=request.user, data=data.get("cart") or {}, label=str(data.get("label") or ""))
    except SaleError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return JsonResponse({"id": held.pk})


@shop_permission_required("pos.create")
def pos_held(request):
    rows = HeldCart.objects.filter(outlet=request.outlet).select_related("held_by")[:50]
    return JsonResponse({"results": [{
        "id": h.pk, "label": h.label or _("Cart %(n)s") % {"n": h.pk}, "by": str(h.held_by),
        "at": timezone.localtime(h.created_at).strftime("%I:%M %p"), "items": len(h.data.get("lines", [])),
        "customer": (h.data.get("customer") or {}).get("text", ""),
    } for h in rows]})


@shop_permission_required("pos.create")
@require_POST
def pos_recall(request):
    data = _json_body(request)
    try:
        cart = recall_cart(request.outlet, data.get("id"))
    except SaleError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return JsonResponse({"cart": cart})


# ---- Cash registers ---------------------------------------------------------------------

@shop_permission_required("pos.create")
@require_POST
def register_open(request):
    counter = Counter.objects.filter(pk=request.POST.get("counter") or 0, outlet=request.outlet).first()
    try:
        start_register(request.user, request.outlet, request.POST.get("opening_cash") or 0, counter=counter,
                       note=request.POST.get("note", ""))
    except SaleError as error:
        messages.error(request, str(error))
    return redirect("sales:pos")


def _register_for(request, pk):
    register = get_object_or_404(CashRegister.objects.filter(outlet_id__in=[o.pk for o in request.allowed_outlets])
                                 .select_related("outlet", "counter", "opened_by"), pk=pk)
    if register.opened_by_id != request.user.pk and not has_perm(request.user, "register.list"):
        raise Http404
    return register


@shop_permission_required("pos.create")
def register_detail(request, pk):
    """Z-report. The cashier who opened it closes it here."""
    register = _register_for(request, pk)
    if request.method == "POST":
        if register.opened_by_id != request.user.pk:
            raise Http404
        try:
            close_register(register, request.POST.get("counted_cash"), note=request.POST.get("note", ""))
        except SaleError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, _("Register closed."))
        return redirect("sales:register", pk=pk)
    summary = register_summary(register)
    difference = (register.counted_cash - register.expected_cash) if register.counted_cash is not None and register.expected_cash is not None else None
    return render(request, "sales/register_detail.html", {"register": register, "summary": summary, "difference": difference})


@shop_permission_required("pos.create")
def register_close_current(request):
    register = open_register(request.user, request.outlet)
    if register is None:
        messages.info(request, _("Your register isn't open."))
        return redirect("sales:pos")
    return redirect("sales:register", pk=register.pk)


@shop_permission_required("pos.create")
def register_list(request):
    registers = CashRegister.objects.filter(outlet_id__in=[o.pk for o in request.allowed_outlets]).select_related("outlet", "opened_by", "counter")
    if not has_perm(request.user, "register.list"):
        registers = registers.filter(opened_by=request.user)
    return render(request, "sales/register_list.html", {"registers": registers[:100]})


# ---- Sales -----------------------------------------------------------------------------

@shop_permission_required("sale.list")
def sale_list(request):
    return render(request, "sales/sale_list.html", {"outlets": request.allowed_outlets})


@shop_permission_required("sale.list")
def sale_data(request):
    settings = get_business_settings(request.tenant)
    queryset = _sales(request).select_related("customer", "outlet", "created_by")
    if request.GET.get("outlet", "").isdigit():
        queryset = queryset.filter(outlet_id=request.GET["outlet"])
    if request.GET.get("due") == "yes":  # sold on due (later collections aren't subtracted here)
        queryset = queryset.filter(customer__isnull=False, paid__lt=F("total"))
    if request.GET.get("date"):
        queryset = queryset.filter(sold_at__date=request.GET["date"])

    def row(s):
        url = reverse("sales:detail", args=[s.pk])
        return [
            format_html('<a class="fw-semibold" href="{}"><code>{}</code></a>', url, s.number),
            timezone.localtime(s.sold_at).strftime("%d %b %Y, %I:%M %p"),
            format_html('{}<small class="d-block text-body-secondary">{}</small>', s.customer.name, s.customer.phone) if s.customer else _("Walk-in"),
            s.outlet.name,
            str(s.created_by or "—"),
            format_money(s.total, settings),
            format_money(s.paid, settings),
            format_html('<span class="text-danger fw-semibold">{}</span>', format_money(s.due, settings)) if s.due > 0 else "—",
        ]

    return datatable_json(
        request, queryset.order_by("-sold_at", "-id"),
        order_columns=["number", "sold_at", "customer__name", "outlet__name", None, "total", "paid", None],
        row=row,
        search=lambda qs, t: qs.filter(Q(number__icontains=t) | Q(customer__name__icontains=t) | Q(customer__phone__icontains=t)),
    )


def _sale_context(sale):
    lines = list(sale.lines.select_related("medicine", "promotion").prefetch_related("allocations__batch"))
    return {
        "sale": sale, "lines": lines,
        "payments": sale.payments.select_related("account"),
        "returns": sale.returns.all(),
        "outstanding": sale_due(sale) if sale.customer_id else Decimal("0"),
    }


@shop_permission_required("sale.list")
def sale_detail(request, pk):
    sale = get_object_or_404(_sales(request).select_related("customer", "outlet", "created_by", "register"), pk=pk)
    return render(request, "sales/sale_detail.html", _sale_context(sale))


@shop_permission_required("sale.list")
def receipt(request, pk):
    """Printable receipt: ?size=58|80|a4 (default from settings), ?print=1 opens the print dialog."""
    sale = get_object_or_404(_sales(request).select_related("customer", "outlet", "created_by"), pk=pk)
    settings = get_business_settings(request.tenant)
    size = request.GET.get("size") if request.GET.get("size") in ("58", "80", "a4") else settings.receipt_size
    context = _sale_context(sale)
    context.update({"size": size, "auto_print": request.GET.get("print") == "1", "shop": settings, "tenant": request.tenant,
                    "total_discount": sale.discount + sale.line_discount})
    return render(request, "sales/receipt.html", context)


@shop_permission_required("sale_return.create")
def return_create(request, pk):
    sale = get_object_or_404(_sales(request).select_related("customer", "outlet"), pk=pk)
    rows = returnable(sale)
    accounts = PaymentAccount.objects.filter(is_active=True)
    error = None
    if request.method == "POST":
        quantities = {}
        for line, _left in rows:
            value = request.POST.get(f"qty_{line.pk}", "").strip()
            if value:
                quantities[line.pk] = int(value) if value.isdigit() else -1
        account = accounts.filter(pk=request.POST.get("refund_account") or 0).first()
        try:
            sale_return = create_return(sale, quantities, date=timezone.localdate(), refund_amount=request.POST.get("refund_amount") or 0,
                                        refund_account=account, reason=request.POST.get("reason", ""),
                                        register=open_register(request.user, request.outlet), user=request.user)
        except SaleError as error_:
            error = str(error_)
        else:
            messages.success(request, _("Return %(number)s saved: stock back on the shelf.") % {"number": sale_return.number})
            return redirect("sales:detail", pk=pk)
    factor = refund_factor(sale)
    lines = [{"line": line, "left": left, "price": (line.net_unit_price * factor).quantize(Decimal("0.01")),
              "posted": request.POST.get(f"qty_{line.pk}", "")} for line, left in rows]
    return render(request, "sales/return_form.html", {"sale": sale, "lines": lines, "accounts": accounts, "error": error,
                                                      "posted": request.POST, "currency": get_business_settings(request.tenant).currency_symbol})


@shop_permission_required("sale_return.list")
def return_list(request):
    return render(request, "sales/return_list.html")


@shop_permission_required("sale_return.list")
def return_data(request):
    settings = get_business_settings(request.tenant)
    queryset = SaleReturn.objects.filter(outlet_id__in=[o.pk for o in request.allowed_outlets]).select_related("sale", "customer", "refund_account")

    def row(r):
        return [
            format_html("<code>{}</code>", r.number), r.date.strftime("%d %b %Y"),
            format_html('<a href="{}">{}</a>', reverse("sales:detail", args=[r.sale_id]), r.sale.number),
            r.customer.name if r.customer else _("Walk-in"), r.reason or "—",
            format_money(r.total, settings), format_money(r.refund_amount, settings),
        ]

    return datatable_json(request, queryset.order_by("-date", "-id"),
                          order_columns=["number", "date", "sale__number", "customer__name", None, "total", "refund_amount"], row=row,
                          search=lambda qs, t: qs.filter(Q(number__icontains=t) | Q(sale__number__icontains=t)))


# ---- Quotations ------------------------------------------------------------------------

def _quotations(request):
    return Quotation.objects.filter(outlet_id__in=[o.pk for o in request.allowed_outlets])


def _quote_badge(quotation, today):
    state = quotation.state(today)
    tone = {"open": "trial", "sold": "active", "expired": "suspended"}[state]
    label = {"open": _("Open"), "sold": _("Sold"), "expired": _("Expired")}[state]
    return format_html('<span class="state-badge state-{}">{}</span>', tone, label)


@shop_permission_required("quotation.list")
def quotation_list(request):
    return render(request, "sales/quotation_list.html")


@shop_permission_required("quotation.list")
def quotation_data(request):
    settings = get_business_settings(request.tenant)
    today = timezone.localdate()
    queryset = _quotations(request).select_related("customer", "outlet", "sale")
    status = request.GET.get("status")
    if status == "open":
        queryset = queryset.filter(status=Quotation.Status.OPEN, valid_until__gte=today)
    elif status == "expired":
        queryset = queryset.filter(status=Quotation.Status.OPEN, valid_until__lt=today)
    elif status == "sold":
        queryset = queryset.filter(status=Quotation.Status.SOLD)

    def row(qt):
        return [
            format_html('<a class="fw-semibold" href="{}"><code>{}</code></a>', reverse("sales:quotation", args=[qt.pk]), qt.number),
            format_shop_date(qt.date, settings),
            qt.customer.name if qt.customer else _("Walk-in"),
            qt.outlet.name,
            format_shop_date(qt.valid_until, settings),
            _quote_badge(qt, today),
            format_money(qt.total, settings),
        ]

    return datatable_json(request, queryset.order_by("-date", "-id"),
                          order_columns=["number", "date", "customer__name", "outlet__name", "valid_until", "status", "total"], row=row,
                          search=lambda qs, t: qs.filter(Q(number__icontains=t) | Q(customer__name__icontains=t) | Q(customer__phone__icontains=t)))


@shop_permission_required("quotation.list")
def quotation_detail(request, pk):
    quotation = get_object_or_404(_quotations(request).select_related("customer", "outlet", "sale", "created_by"), pk=pk)
    today = timezone.localdate()
    return render(request, "sales/quotation_detail.html", {
        "quotation": quotation, "lines": quotation.lines.select_related("medicine"), "state": quotation.state(today),
        "badge": _quote_badge(quotation, today),
        "can_sell": quotation.status == Quotation.Status.OPEN and has_perm(request.user, "pos.create")
                    and any(o.pk == quotation.outlet_id for o in request.allowed_outlets),
    })


@shop_permission_required("quotation.list")
def quotation_print(request, pk):
    quotation = get_object_or_404(_quotations(request).select_related("customer", "outlet", "created_by"), pk=pk)
    return render(request, "sales/quotation_print.html", {
        "quotation": quotation, "lines": quotation.lines.select_related("medicine"),
        "shop": get_business_settings(request.tenant), "tenant": request.tenant, "auto_print": request.GET.get("print") == "1",
    })


@shop_permission_required("quotation.delete")
@require_POST
def quotation_delete(request, pk):
    quotation = get_object_or_404(_quotations(request), pk=pk, status=Quotation.Status.OPEN)
    number = quotation.number
    quotation.delete()
    messages.success(request, _("Quotation %(number)s deleted.") % {"number": number})
    return redirect("sales:quotations")


# ---- Promotions -------------------------------------------------------------------------

@shop_permission_required("promotion.list")
def promotion_list(request):
    today = timezone.localdate()
    promotions = list(Promotion.objects.select_related("category").prefetch_related("medicines"))
    labels = dict(DosageForm.choices)
    for promotion in promotions:
        promotion.state = promotion.status(today)
        promotion.target = {
            Promotion.Scope.ALL: _("Every medicine"),
            Promotion.Scope.CATEGORY: promotion.category.name if promotion.category else "—",
            Promotion.Scope.FORM: labels.get(promotion.dosage_form, promotion.dosage_form),
            Promotion.Scope.MEDICINES: ", ".join(m.display_name for m in list(promotion.medicines.all())[:3])
            + (" …" if promotion.medicines.count() > 3 else ""),
        }[promotion.scope]
    return render(request, "sales/promotion_list.html", {"promotions": promotions})


class PromotionCreateView(ShopCreateView):
    permission = "promotion.create"
    form_class = PromotionForm
    template_name = "sales/promotion_form.html"
    title = gettext_lazy("New promotion")
    success_url = reverse_lazy("sales:promotions")


class PromotionUpdateView(ShopUpdateView):
    permission = "promotion.edit"
    form_class = PromotionForm
    template_name = "sales/promotion_form.html"
    title = gettext_lazy("Edit promotion")
    success_url = reverse_lazy("sales:promotions")

    def get_queryset(self):
        return Promotion.objects.all()


@shop_permission_required("promotion.edit")
@require_POST
def promotion_toggle(request, pk):
    promotion = get_object_or_404(Promotion.objects.all(), pk=pk)
    promotion.is_active = not promotion.is_active
    promotion.save(update_fields=["is_active", "updated_at"])
    messages.success(request, (_("%(name)s switched on.") if promotion.is_active else _("%(name)s switched off.")) % {"name": promotion.name})
    return redirect("sales:promotions")


@shop_permission_required("promotion.delete")
@require_POST
def promotion_delete(request, pk):
    promotion = get_object_or_404(Promotion.objects.all(), pk=pk)
    name = promotion.name
    promotion.delete()
    messages.success(request, _("%(name)s deleted. Past sales keep their discounts.") % {"name": name})
    return redirect("sales:promotions")
