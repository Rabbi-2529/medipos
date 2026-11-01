"""The POS without internet: a copy of what the till needs to sell, and taking in the sales it made meanwhile.

The till keeps the copy in the browser (IndexedDB), sells from it when the server can't be reached, and sends the sales
here when the connection is back. Each offline sale carries the till's own id (offline_ref), so a sale sent twice is
recorded once. The server checks everything again: a sale it can't take (stock gone, credit limit) comes back with the
reason and stays on the till for the staff to sort out."""
from datetime import datetime, timedelta

from django.db.models import Sum
from django.http import JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.customers.models import Customer
from apps.customers.services import with_balances
from apps.messaging.services import after_sale
from apps.people.access import allowed_outlets, shop_permission_required

from .models import CashRegister, Sale, SaleLine
from .services import SaleError, complete_sale, open_register, running_promotions
from .views import _json_body, _pos_medicines, _pos_row

MAX_BATCH = 50
OLDEST = timedelta(days=30)   # an offline sale older than this is refused (the till's clock or a forgotten queue)
CLOCK_SLACK = timedelta(minutes=10)


@shop_permission_required("pos.create")
def offline_data(request):
    """Everything the till needs to keep selling without internet, for the outlet it is in."""
    outlet = request.outlet
    promotions = running_promotions()
    since = timezone.now() - timedelta(days=30)
    ranked = {row["medicine"]: row["units"] for row in SaleLine.objects.filter(sale__outlet=outlet, sale__sold_at__gte=since)
              .values("medicine").annotate(units=Sum("quantity"))}
    medicines = []
    for m in _pos_medicines(outlet).order_by("name"):
        row = _pos_row(m, promotions)
        row.update({"barcode": m.barcode, "category": m.category_id, "form": m.dosage_form, "sold": ranked.get(m.pk, 0)})
        medicines.append(row)
    customers = [{"id": c.pk, "text": str(c), "name": c.name, "phone": c.phone, "balance": str(c.balance),
                  "credit_limit": None if c.credit_limit is None else str(c.credit_limit), "default_discount": str(c.default_discount),
                  "points": c.points} for c in with_balances(Customer.objects.filter(is_active=True)).order_by("name")]
    register = open_register(request.user, outlet)
    return JsonResponse({
        "tenant": request.tenant.pk, "user": request.user.pk, "outlet": outlet.pk, "outletName": outlet.name,
        "register": register.pk if register else None, "at": timezone.now().isoformat(),
        "medicines": medicines, "customers": customers,
    })


def _when(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if timezone.is_aware(moment) else timezone.make_aware(moment)


def take_sale(request, data):
    """One offline sale -> {"offlineId", "ok", "number"|"error"}. Never raises."""
    offline_id = str(data.get("offlineId") or "").strip()[:40]
    if not offline_id:
        return {"offlineId": offline_id, "ok": False, "error": _("This sale has no id.")}
    done = Sale.objects.filter(offline_ref=offline_id).first()
    if done is not None:
        return {"offlineId": offline_id, "ok": True, "number": done.number, "id": done.pk, "already": True}
    sold_at = _when(data.get("soldAt"))
    now = timezone.now()
    if sold_at is None or sold_at > now + CLOCK_SLACK or sold_at < now - OLDEST:
        return {"offlineId": offline_id, "ok": False, "error": _("The sale's time is wrong. Check this computer's clock.")}
    outlet = allowed_outlets(request.user).filter(pk=data.get("outlet")).first() if str(data.get("outlet") or "").isdigit() else None
    if outlet is None:
        return {"offlineId": offline_id, "ok": False, "error": _("You can't sell in that outlet any more.")}
    # The till the money went into: the one open when it was sold, else this person's open one there.
    register = None
    if str(data.get("register") or "").isdigit():
        register = CashRegister.objects.filter(pk=data["register"], outlet=outlet, opened_by=request.user).first()
    register = register or open_register(request.user, outlet)
    customer = Customer.objects.filter(pk=data["customer"], is_active=True).first() if str(data.get("customer") or "").isdigit() else None
    note = _("Sold without internet") + (f" · {data['note']}" if data.get("note") else "")
    try:
        sale = complete_sale(
            tenant=request.tenant, outlet=outlet, register=register, user=request.user, lines=data.get("lines") or [],
            payments=data.get("payments") or [], customer=customer, price_type=data.get("priceType") or Sale.PriceType.RETAIL,
            cart_discount=data.get("discount") or 0, cash_tendered=data.get("cashTendered"), rx_checked=bool(data.get("rxChecked")),
            note=str(note), now=sold_at, points=data.get("points") or 0, offline_ref=offline_id)
    except SaleError as error:
        return {"offlineId": offline_id, "ok": False, "error": str(error)}
    after_sale(sale, base_url=request.build_absolute_uri("/"), user=request.user)
    return {"offlineId": offline_id, "ok": True, "number": sale.number, "id": sale.pk}


@shop_permission_required("pos.create")
@require_POST
def sync(request):
    """The till sends the sales it made offline (oldest first); each comes back with its invoice number or the reason."""
    sales = _json_body(request).get("sales") or []
    if not isinstance(sales, list):
        sales = []
    return JsonResponse({"results": [take_sale(request, data) for data in sales[:MAX_BATCH] if isinstance(data, dict)]})
