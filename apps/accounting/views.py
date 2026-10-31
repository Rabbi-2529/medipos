import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse, reverse_lazy
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from apps.core.datatables import datatable_json
from apps.core.shop_views import ShopCreateView, ShopListView, ShopToggleView, ShopUpdateView
from apps.outlets.models import Outlet
from apps.people.access import has_perm, shop_permission_required
from apps.setup.services import format_money, format_shop_date, get_business_settings

from . import posting, reports
from .chart import category_accounts, ensure_chart, money_account
from .forms import AccountForm, CashEntryForm, DateRangeForm, FundMovementForm, ManualEntryForm, PaymentAccountForm
from .models import Account, CashEntry, FundMovement, JournalEntry, PaymentAccount
from .services import (
    AccountingError, add_category, create_manual_entry, delete_cash_entry, delete_fund_movement, delete_manual_entry, move_funds,
    record_cash_entry, rename_category,
)

ACCOUNTS = reverse_lazy("accounting:payment_accounts")


# ---- Payment accounts ----------------------------------------------------------------

class PaymentAccountListView(ShopListView):
    permission = "payment_account.list"
    template_name = "accounting/payment_account_list.html"
    title = gettext_lazy("Payment accounts")

    def get_queryset(self):
        return PaymentAccount.objects.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ensure_chart()
        balances = reports.sums(accounts=Account.objects.filter(payment_account__isnull=False))
        for account in context["object_list"]:
            ledger = money_account(account)
            debit, credit = balances.get(ledger.pk, (0, 0))
            account.balance, account.ledger_id = debit - credit, ledger.pk
        return context


class _BooksMixin:
    """A payment account has its own ledger account; its opening balance is posted against "Opening balances"."""

    def form_valid(self, form):
        response = super().form_valid(form)
        ledger = money_account(self.object)
        if ledger.name != self.object.name:
            ledger.name = self.object.name
            ledger.save(update_fields=["name", "updated_at"])
        posting.post_payment_account_opening(self.object)
        return response


class PaymentAccountCreateView(_BooksMixin, ShopCreateView):
    permission = "payment_account.create"
    form_class = PaymentAccountForm
    title = gettext_lazy("Add payment account")
    success_url = ACCOUNTS


class PaymentAccountUpdateView(_BooksMixin, ShopUpdateView):
    permission = "payment_account.edit"
    form_class = PaymentAccountForm
    title = gettext_lazy("Edit payment account")
    success_url = ACCOUNTS

    def get_queryset(self):
        return PaymentAccount.objects.all()


class PaymentAccountToggleView(ShopToggleView):
    permission = "payment_account.delete"
    model = PaymentAccount
    success_url = ACCOUNTS

    def can_deactivate(self, account):
        if not PaymentAccount.objects.filter(is_active=True).exclude(pk=account.pk).exists():
            return _("Keep at least one active payment account, or sales can't be paid.")
        return None


# ---- Income, expenses, deposits -----------------------------------------------------------

def _register_for(request, payment_account):
    """Cash paid in or out of the drawer belongs to the user's open register, so the Z-report counts it."""
    if payment_account.kind != PaymentAccount.Kind.CASH or request.outlet is None:
        return None
    from apps.sales.services import open_register

    return open_register(request.user, request.outlet)


def _cash_entry_views(kind):
    code = kind  # permission module: "expense" or "income"
    template = "accounting/cash_entry_list.html"

    @shop_permission_required(f"{code}.list")
    def entry_list(request):
        ensure_chart()
        settings = get_business_settings(request.tenant)
        form = CashEntryForm(request.POST or None, kind=kind, outlets=request.allowed_outlets,
                             initial={"outlet": getattr(request.outlet, "pk", None)})
        if request.method == "POST":
            if not has_perm(request.user, f"{code}.create"):
                raise Http404
            if form.is_valid():
                data = form.cleaned_data
                try:
                    entry = record_cash_entry(tenant=request.tenant, kind=kind, date=data["date"], account=data["account"],
                                              payment_account=data["payment_account"], amount=data["amount"], outlet=data["outlet"],
                                              register=_register_for(request, data["payment_account"]),
                                              reference=data["reference"], note=data["note"], user=request.user)
                except AccountingError as error:
                    form.add_error(None, str(error))
                else:
                    messages.success(request, _("%(number)s saved: %(amount)s, %(category)s.") % {
                        "number": entry.number, "amount": format_money(entry.amount, settings), "category": entry.account.label})
                    return redirect(request.path)
        start, end = _range(request)
        entries = CashEntry.objects.filter(kind=kind, date__gte=start, date__lte=end).select_related("account", "payment_account", "outlet", "created_by")
        by_category = (entries.values("account").annotate(t=Sum("amount")).order_by("-t"))
        names = {a.pk: a.label for a in Account.objects.filter(pk__in=[r["account"] for r in by_category])}
        return render(request, template, {
            "kind": kind, "form": form, "entries": entries, "start": start, "end": end,
            "total": entries.aggregate(t=Sum("amount"))["t"] or 0,
            "by_category": [(names[r["account"]], r["t"]) for r in by_category],
            "can_create": has_perm(request.user, f"{code}.create"), "can_delete": has_perm(request.user, f"{code}.delete"),
        })

    @shop_permission_required(f"{code}.delete")
    @require_POST
    def entry_delete(request, pk):
        entry = get_object_or_404(CashEntry.objects.filter(kind=kind), pk=pk)
        delete_cash_entry(entry)
        messages.success(request, _("%(number)s deleted.") % {"number": entry.number})
        return redirect(f"accounting:{'incomes' if kind == 'income' else 'expenses'}")

    return entry_list, entry_delete


expense_list, expense_delete = _cash_entry_views(CashEntry.Kind.EXPENSE)
income_list, income_delete = _cash_entry_views(CashEntry.Kind.INCOME)


# ---- Expense and income categories ---------------------------------------------------------
# A category is an expense/income account in the chart; these screens hide the codes and types.

def _category_views(kind):
    code = kind  # permission module: "expense" or "income"; adding and renaming needs "<kind>.create"
    account_type = Account.Type.INCOME if kind == CashEntry.Kind.INCOME else Account.Type.EXPENSE
    list_url = f"accounting:{kind}_categories"

    @shop_permission_required(f"{code}.list")
    def category_list(request):
        ensure_chart()
        if request.method == "POST":
            if not has_perm(request.user, f"{code}.create"):
                raise Http404
            try:
                category = add_category(kind, request.POST.get("name"), request.POST.get("description", ""))
            except AccountingError as error:
                messages.error(request, str(error))
            else:
                messages.success(request, _("“%(name)s” added.") % {"name": category.label})
            return redirect(list_url)
        today = timezone.localdate()
        stats = {r["account"]: r for r in CashEntry.objects.filter(kind=kind).values("account").annotate(
            count=Count("id"), month=Sum("amount", filter=Q(date__gte=today.replace(day=1))))}
        categories = list(category_accounts(account_type).select_related("payment_account").order_by("-is_active", "code"))
        for category in categories:
            row = stats.get(category.pk, {})
            category.entry_count, category.month_total = row.get("count", 0), row.get("month") or 0
        return render(request, "accounting/category_list.html", {
            "kind": kind, "categories": categories, "can_manage": has_perm(request.user, f"{code}.create"),
        })

    @shop_permission_required(f"{code}.create")
    @require_POST
    def category_edit(request, pk):
        category = get_object_or_404(category_accounts(account_type), pk=pk)
        try:
            rename_category(category, kind, request.POST.get("name"), request.POST.get("description", ""))
        except AccountingError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, _("Saved."))
        return redirect(list_url)

    @shop_permission_required(f"{code}.create")
    @require_POST
    def category_toggle(request, pk):
        category = get_object_or_404(category_accounts(account_type), pk=pk)
        category.is_active = not category.is_active
        category.save(update_fields=["is_active", "updated_at"])
        messages.success(request, (_("“%(name)s” can be picked again.") if category.is_active
                                   else _("“%(name)s” is hidden from new entries; past entries keep it.")) % {"name": category.label})
        return redirect(list_url)

    @shop_permission_required(f"{code}.create")
    @require_POST
    def category_quick(request):
        """The "+ New category" box on the expense/income form: JSON in, the new option out."""
        try:
            data = json.loads(request.body or "{}")
            category = add_category(kind, str(data.get("name", "")))
        except (AccountingError, json.JSONDecodeError, AttributeError) as error:
            return JsonResponse({"error": str(error) if isinstance(error, AccountingError) else _("Enter a name.")}, status=400)
        return JsonResponse({"id": category.pk, "text": category.label})

    return category_list, category_edit, category_toggle, category_quick


expense_categories, expense_category_edit, expense_category_toggle, expense_category_quick = _category_views(CashEntry.Kind.EXPENSE)
income_categories, income_category_edit, income_category_toggle, income_category_quick = _category_views(CashEntry.Kind.INCOME)


@shop_permission_required("deposit_withdraw.list")
def transfer_list(request):
    settings = get_business_settings(request.tenant)
    form = FundMovementForm(request.POST or None)
    if request.method == "POST":
        if not has_perm(request.user, "deposit_withdraw.create"):
            raise Http404
        if form.is_valid():
            data = form.cleaned_data
            cash = next((a for a in (data["from_account"], data["to_account"]) if a and a.kind == PaymentAccount.Kind.CASH), None)
            try:
                movement = move_funds(tenant=request.tenant, kind=data["kind"], date=data["date"], amount=data["amount"],
                                      from_account=data["from_account"], to_account=data["to_account"], note=data["note"],
                                      register=_register_for(request, cash) if cash else None, user=request.user)
            except AccountingError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, _("%(number)s saved: %(amount)s.") % {
                    "number": movement.number, "amount": format_money(movement.amount, settings)})
                return redirect("accounting:transfers")
    start, end = _range(request)
    movements = FundMovement.objects.filter(date__gte=start, date__lte=end).select_related("from_account", "to_account", "created_by")
    return render(request, "accounting/transfer_list.html", {
        "form": form, "movements": movements, "start": start, "end": end,
        "can_create": has_perm(request.user, "deposit_withdraw.create"), "can_delete": has_perm(request.user, "deposit_withdraw.delete"),
    })


@shop_permission_required("deposit_withdraw.delete")
@require_POST
def transfer_delete(request, pk):
    movement = get_object_or_404(FundMovement.objects.all(), pk=pk)
    delete_fund_movement(movement)
    messages.success(request, _("%(number)s deleted.") % {"number": movement.number})
    return redirect("accounting:transfers")


# ---- Chart of accounts ------------------------------------------------------------------

@shop_permission_required("accounting.list")
def chart(request):
    ensure_chart()
    totals = reports.sums()
    groups = []
    headings = {"asset": _("Assets"), "liability": _("Liabilities"), "equity": _("Equity"), "income": _("Income"), "expense": _("Expenses")}
    for kind, _label in Account.Type.choices:
        label = headings[kind]
        rows = []
        for account in Account.objects.filter(type=kind).select_related("payment_account").order_by("code"):
            debit, credit = totals.get(account.pk, (0, 0))
            rows.append({"account": account, "balance": reports.natural(account, debit, credit)})
        groups.append({"type": kind, "label": label, "rows": rows})
    return render(request, "accounting/chart.html", {"groups": groups})


class AccountCreateView(ShopCreateView):
    permission = "accounting.edit"
    form_class = AccountForm
    title = gettext_lazy("New account")
    success_url = reverse_lazy("accounting:chart")


class AccountUpdateView(ShopUpdateView):
    permission = "accounting.edit"
    form_class = AccountForm
    title = gettext_lazy("Edit account")
    success_url = reverse_lazy("accounting:chart")

    def get_queryset(self):
        # Accounts the app posts to (and payment accounts' ledgers) are managed by the app.
        return Account.objects.filter(system_key="", payment_account__isnull=True)


# ---- Journal ---------------------------------------------------------------------------

def _source_link(entry):
    url_name = reports.SOURCES.get(entry.source_type, (None, None))[1]
    if url_name and entry.source_id:
        try:
            return reverse(url_name, args=[entry.source_id])
        except NoReverseMatch:
            return None
    return None


@shop_permission_required("accounting.list")
def journal(request):
    start, end = _range(request)
    return render(request, "accounting/journal.html", {"sources": [(k, v[0]) for k, v in reports.SOURCES.items()], "start": start, "end": end})


@shop_permission_required("accounting.list")
def journal_data(request):
    settings = get_business_settings(request.tenant)
    start, end = _range(request)
    queryset = JournalEntry.objects.filter(date__gte=start, date__lte=end).annotate(amount=Sum("lines__debit"))
    if request.GET.get("source", "__all__") != "__all__" and request.GET.get("source") in reports.SOURCES:
        queryset = queryset.filter(source_type=request.GET["source"])

    def row(entry):
        return [
            format_html('<a class="fw-semibold" href="{}"><code>{}</code></a>', reverse("accounting:entry", args=[entry.pk]), entry.number),
            format_shop_date(entry.date, settings),
            str(reports.source_label(entry.source_type)),
            format_html("{}<small class='d-block text-body-secondary'>{}</small>", entry.memo or "—", entry.source_label),
            format_money(entry.amount or 0, settings),
        ]

    return datatable_json(request, queryset.order_by("-date", "-id"), order_columns=["number", "date", "source_type", None, "amount"],
                          row=row, search=lambda qs, t: qs.filter(Q(number__icontains=t) | Q(memo__icontains=t) | Q(source_label__icontains=t)))


@shop_permission_required("accounting.list")
def entry_detail(request, pk):
    entry = get_object_or_404(JournalEntry.objects.select_related("outlet", "created_by"), pk=pk)
    lines = list(entry.lines.select_related("account", "account__payment_account"))
    return render(request, "accounting/entry_detail.html", {
        "entry": entry, "lines": lines, "source_url": _source_link(entry), "source_name": reports.source_label(entry.source_type),
        "debit": sum(l.debit for l in lines), "credit": sum(l.credit for l in lines),
    })


@shop_permission_required("accounting.create")
def entry_create(request):
    ensure_chart()
    form = ManualEntryForm(request.POST or None)
    accounts = list(Account.objects.filter(is_active=True).select_related("payment_account").order_by("code"))
    posted = []
    if request.method == "POST":
        try:
            posted = json.loads(request.POST.get("lines") or "[]")
        except json.JSONDecodeError:
            posted = []
        if form.is_valid():
            by_id = {a.pk: a for a in accounts}
            try:
                lines = []
                for raw in posted if isinstance(posted, list) else []:
                    account = by_id.get(int(raw.get("account") or 0)) if str(raw.get("account") or "").isdigit() else None
                    if account is None:
                        continue
                    lines.append((account, Decimal(str(raw.get("debit") or 0)), Decimal(str(raw.get("credit") or 0)), str(raw.get("memo") or "")))
                entry = create_manual_entry(tenant=request.tenant, date=form.cleaned_data["date"], memo=form.cleaned_data["memo"],
                                            lines=lines, user=request.user)
            except (AccountingError, InvalidOperation) as error:
                form.add_error(None, str(error) if isinstance(error, AccountingError) else _("Enter valid amounts."))
            else:
                messages.success(request, _("%(number)s saved.") % {"number": entry.number})
                return redirect("accounting:entry", pk=entry.pk)
    return render(request, "accounting/entry_form.html", {
        "form": form, "accounts": [{"id": a.pk, "text": str(a)} for a in accounts], "posted": posted,
        "currency": get_business_settings(request.tenant).currency_symbol,
    })


@shop_permission_required("accounting.create")
@require_POST
def entry_delete(request, pk):
    entry = get_object_or_404(JournalEntry.objects.filter(source_type=""), pk=pk)
    delete_manual_entry(entry)
    messages.success(request, _("%(number)s deleted.") % {"number": entry.number})
    return redirect("accounting:journal")


# ---- Reports ---------------------------------------------------------------------------

def _range(request):
    return DateRangeForm(request.GET).range()


def _as_of(request):
    try:
        return date.fromisoformat(request.GET.get("as_of", ""))
    except ValueError:
        return timezone.localdate()


@shop_permission_required("accounting.list")
def statement(request):
    ensure_chart()
    accounts = list(Account.objects.select_related("payment_account").order_by("code"))
    chosen = request.GET.get("account", "")
    account = next((a for a in accounts if str(a.pk) == chosen), None) or next((a for a in accounts if a.payment_account_id), accounts[0])
    start, end = _range(request)
    return render(request, "accounting/statement.html", {
        "accounts": accounts, "account": account, "start": start, "end": end, "report": reports.statement(account, start, end),
    })


@shop_permission_required("accounting.list")
def trial_balance(request):
    ensure_chart()
    as_of = _as_of(request)
    return render(request, "accounting/trial_balance.html", {"as_of": as_of, "report": reports.trial_balance(as_of)})


@shop_permission_required("accounting.list")
def profit_loss(request):
    ensure_chart()
    start, end = _range(request)
    outlet = Outlet.objects.filter(pk=request.GET.get("outlet")).first() if str(request.GET.get("outlet", "")).isdigit() else None
    return render(request, "accounting/profit_loss.html", {
        "start": start, "end": end, "outlet": outlet, "outlets": Outlet.objects.filter(is_active=True),
        "report": reports.profit_and_loss(start, end, outlet),
    })


@shop_permission_required("accounting.list")
def balance_sheet(request):
    ensure_chart()
    as_of = _as_of(request)
    return render(request, "accounting/balance_sheet.html", {"as_of": as_of, "report": reports.balance_sheet(as_of)})


@shop_permission_required("accounting.list")
def cash_flow(request):
    ensure_chart()
    start, end = _range(request)
    return render(request, "accounting/cash_flow.html", {"start": start, "end": end, "report": reports.cash_flow(start, end)})
