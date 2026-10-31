# ThingBD MediPOS

ThingBD's multi-tenant SaaS for medicine shops: inventory, POS, purchases, accounting and reports.
The build plan is in [docs/PLAN.md](docs/PLAN.md).

## Run locally (Windows)

```bash
py -3.13 -m venv venv
venv/Scripts/python.exe -m pip install -r requirements.txt
copy .env.example .env          # then set SECRET_KEY
venv/Scripts/python.exe manage.py migrate
venv/Scripts/python.exe manage.py seed_demo
venv/Scripts/python.exe manage.py runserver
```

Open http://localhost:8000. `seed_demo` creates:

| Login | Role |
|---|---|
| `admin@medipos.local` | Platform admin: console at `/platform/` |
| `owner@lazzpharma.local` | "Lazz Pharma (Demo)", paid on Business, two outlets |
| `manager@lazzpharma.local` | Lazz staff: Manager role, all outlets |
| `cashier@lazzpharma.local` | Lazz staff: Cashier role, Main branch only |
| `owner@greenlife.local` | "Green Life Pharmacy (Demo)", trial ending in 2 days |
| `owner@sunrise.local` | "Sunrise Pharmacy (Demo)", trial expired (locked out) |

All of them use the `DEMO_PASSWORD` from `.env`. New pharmacies sign up at `/signup/`, or the platform admin adds them under **Pharmacies → Add pharmacy**.

Lazz Pharma also gets two customers, an open cash register for the owner and a few sales, so the POS (`/sales/pos/`) works straight away.

## Subscriptions

- Trial length, grace days and the "how to pay" text are set in `.env` (`TRIAL_DAYS`, `SUBSCRIPTION_GRACE_DAYS`, `SUBSCRIPTION_PAYMENT_INSTRUCTIONS`).
- Payments are recorded by the platform admin on the pharmacy's page. Each payment extends "paid until" and reactivates the account.
- Before creating outlets, users or medicines, call `apps.billing.limits.check_limit(tenant, resource)`.

## Tests

```bash
venv/Scripts/python.exe manage.py test --settings=config.settings.test
```

## Layout

```
config/            settings (base/dev/prod/test), urls
apps/core/         tenancy (context, TenantOwnedModel, middleware), menu registry, dashboard
apps/tenants/      Tenant (pharmacy) + access state, self-signup, seed_demo command
apps/accounts/     custom User (email/phone login), auth views
apps/billing/      plans, payments, subscription services, plan limits, access gate
apps/console/      platform console for ThingBD staff (/platform/)
apps/outlets/      outlets, counters, current-outlet middleware and switcher
apps/people/       employees, roles, permission checks (access.py)
apps/setup/        business settings, document numbering, tenant provisioning
apps/accounting/   payment accounts, chart of accounts, journals, expenses/income, fund movements, reports
apps/items/        medicines, setup lists, import/export, bulk prices, labels
apps/catalog/      platform-wide medicine catalog (fixtures/sample_catalog.csv = demo sample)
apps/stock/        batches, per-outlet stock, movement ledger, opening-stock import
apps/suppliers/    suppliers, supplier ledger, payments
apps/purchases/    purchases (draft → received), purchase returns
apps/customers/    customers, customer ledger, due collection
apps/sales/        cash registers, POS, sales, held carts, sale returns, promotions, quotations
apps/inventory/    transfers between outlets, damage/expired write-offs, stock counts
locale/bn/         Bangla translations (django.po source, django.mo compiled)
templates/         layouts, partials, pages
static/            css/app.css (theme tokens, dark mode), js/app.js, js/pos.js, js/stock_lines.js
```

## Languages (English ⇄ বাংলা)

- Logged-in users pick a language in the top bar; it's saved on their account. Otherwise the pharmacy's default from Business settings applies.
- Wrap every user-facing string: `{% translate %}` / `{% blocktranslate %}` in templates, `gettext` in Python. Use `context` when one English word means two things, e.g. `{% translate "Open" context "register state" %}`.
- For JavaScript strings, build a dict in the view and pass it with `json_script` (see `js_texts()` and `pos_texts()`).
- After changing strings:

```bash
venv/Scripts/python.exe manage.py translations extract   # update locale/bn/LC_MESSAGES/django.po
venv/Scripts/python.exe manage.py translations stats     # how many are still untranslated
venv/Scripts/python.exe manage.py translations compile   # build django.mo (checks %(name)s placeholders)
```

Fill in the empty `msgstr ""` entries in `django.po` before compiling. The command uses Babel, so GNU gettext isn't needed.

## Tenancy rules (read before adding models)

- Pharmacy data inherits `apps.core.models.TenantOwnedModel`. Its `objects` manager only sees the current tenant's rows.
- Web requests get the tenant from the logged-in user automatically.
- Celery tasks and management commands must wrap their work in `tenant_context(tenant)`.
- Use `bypass_tenant()` or `Model.all_objects` only in platform-admin code.
- Related managers, e.g. `employee.outlets.all()`, are tenant-scoped too. Outside a request, wrap them in `tenant_context`.
- Forms:
  - Use `apps.core.forms.TenantModelForm`. It validates per-tenant unique fields and fixes empty choice lists.
  - In plain `Form`s, set querysets of tenant models in `__init__`, never on the class.

## Medicine catalog

`seed_demo` loads a 28-row sample catalog with no prices, and Lazz Pharma adopts it with made-up demo prices.

For real use:
1. Sign in as platform admin.
2. Open **Medicine catalog → Import**.
3. Upload an .xlsx/.csv with these columns: Brand name, Generic, Strength, Dosage form, Manufacturer, Pack size, Unit price.

Use a list ThingBD has the right to use, such as DGDA's registered-drug list.

## Stock rules

- Change stock **only** through `apps.stock.services.post_movement()`.
  - It locks the outlet × batch row, refuses to go below zero (`StockError`) and writes a `StockMovement`.
  - Quantities are always in sale units, e.g. pieces.
- Sales take stock earliest-expiry-first (`apps.sales.services.allocate()`) and never from expired batches. Returns put it back into the batches it came from.
- Transfers, write-offs and stock counts go through `apps.inventory.services` (which calls `post_movement()`). A transfer takes stock out on dispatch and puts it in on receive; until then it is "in transit".
- Document numbers come from `apps.setup.services.next_code(tenant, key, prefix=…)`: INV (invoice sequence from settings), PUR-, PRT-, SPY-, SRT-, RCV-, TRF-, DMG-, ADJ-, QT-.
- Supplier balances come from `SupplierLedgerEntry` (credit = owed, debit = paid/returned). Post entries with `suppliers.services.post_entry()`.
- Customer balances come from `CustomerLedgerEntry` (debit = owes, credit = paid/returned). Post entries with `customers.services.post_entry()`.

## Accounting rules

- Every document that moves money or stock posts **one balanced journal entry** through `apps.accounting.posting` (e.g. `post_sale(sale)`), called at the end of its service. Posting again replaces the document's entry.
- A new document type needs a `post_…` function, a call in its service, and a line in `posting.rebuild()`.
- Use `chart.system_account("sales")` / `chart.money_account(payment_account)`; never look accounts up by code.
- After changing posting rules, or on a database that has documents but no journals:

```bash
venv/Scripts/python.exe manage.py rebuild_books            # every pharmacy
venv/Scripts/python.exe manage.py rebuild_books --tenant lazz-pharma-demo
```

## Forms and dates

- `<input type="date">` and `<input type="month">` become a themed flatpickr calendar automatically (`MP.initDatePickers`, also after HTMX swaps). Add `data-native` to keep the browser's picker. Scripts that rebuild rows call `MP.destroyDatePickers(row)` first and `MP.initDatePickers(row)` after.
- Selects with `data-mp-select` become Select2 (`data-ajax-url` for server search, `data-tags` to allow new values).

## Permissions

- Add each new screen's module to `apps/core/permissions.py` (`Module(key, label, group, actions)`).
- Protect views with `shop_permission_required("<module>.<action>")` or `ShopPermissionMixin`.
- In templates, use `{% if can.<module>.<action> %}`.
- Sidebar items whose `key` matches a module are filtered automatically.
