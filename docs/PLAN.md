# ThingBD MediPOS — build plan

Multi-tenant SaaS for medicine shops (pharmacies): inventory, POS, purchases, accounting and reports.
Modeled on the Doorsoft "Retail POS – medicine shop" demo, trimmed to what a pharmacy needs and
extended with batch numbers, FEFO, prescription flags and a Bangladeshi medicine catalog.

Each phase ends with tests green and a check in the browser before the next one starts.

## Architecture decisions

| Topic | Decision |
|---|---|
| Stack | Django 5.2 + DRF, PostgreSQL (SQLite in dev), Bootstrap 5 theme, HTMX, Celery + Redis (from Phase 6) |
| Tenancy | Shared database with row-level isolation. A **tenant** is one pharmacy business. All its data inherits `TenantOwnedModel`. |
| Isolation | `TenantManager` filters every query by the current tenant (a ContextVar set by `TenantMiddleware`). It **fails closed**: no tenant means no rows, and saving without a tenant raises an error. Cross-tenant access only through explicit `bypass_tenant()` / `all_objects`. |
| Users | Login by email or phone. Pharmacy staff belong to one tenant. Platform admins (the SaaS operator) have no tenant. |
| Outlets | A tenant has many outlets (branches). Staff "enter" an outlet. Stock and sales are per outlet. |
| Stock | Immutable stock ledger plus per-outlet × item × **batch + expiry** balances. Selling picks first-expiry-first-out (FEFO). Expired stock can't be sold. |
| UI | Select2 for dropdowns, server-side DataTables for lists, FullCalendar for dates. Menu registry in `apps/core/menu.py`: menus appear when their URL exists. |
| Money | Decimal everywhere, currency TK by default. Double-entry journals posted by services. |

## Phases

### Phase 0 — Foundation and tenancy core ✅
Custom user (email/phone login), Tenant model, fail-closed tenant scoping and middleware, base layout
(sidebar, top bar, Ctrl+K menu search, dark mode, mobile drawer), login/logout/profile/password change/reset,
`seed_demo` command, 32 tests including tenant-isolation tests.

### Phase 1 — SaaS platform and onboarding ✅
- **Self-signup** at `/signup/`: creates the pharmacy, its owner (`is_owner`) and a "Main branch" outlet, and starts a `TRIAL_DAYS` trial. Includes a honeypot against bots.
- **Plans** (`billing.Plan`) with monthly/yearly prices and outlet/user/medicine limits (empty = unlimited). Three placeholder plans are seeded by migration.
- **Access state** is computed from the stored status plus dates: trial → active → payment due (`SUBSCRIPTION_GRACE_DAYS`) → expired, or suspended.
  - `Tenant.access_state()` and `Tenant.objects.in_state()` are kept in agreement by tests.
  - `SubscriptionGateMiddleware` sends expired or suspended pharmacies to `/subscription/`. HTMX and API requests get a 402.
  - A banner warns before a trial or paid period ends.
- **Plan limits**: `billing.limits.check_limit(tenant, "users"|"outlets"|"medicines")`. Phase 2+ must call it before creating any of these.
- **Platform console** at `/platform/`:
  - **Add pharmacy** (`/platform/pharmacies/new/`): staff create a pharmacy, owner login and outlet for a customer, with any active plan and a chosen trial length. Django admin's "add" is disabled, because it would create a pharmacy with no owner.
  - pharmacy list (server-side DataTables, Select2 filters)
  - pharmacy detail with usage, payments and an activity log
  - actions: record payment, change plan, extend trial, suspend, reactivate
  - plan management
  - dashboard overview: counts, revenue this month, what's ending this week
- **Subscription page** for pharmacies: plan, status, usage, payment history, how to pay.
- 80 tests.

### Phase 2 — Shop setup and access ✅
- **Permissions**:
  - The registry `core/permissions.py` lists modules and their actions; codes look like `outlet.create`.
  - `Role.permissions` stores codes. Codes for future modules are kept and switch on when their phase registers the module.
  - `people.access` provides `has_perm`, `shop_permission_required` and `ShopPermissionMixin`, plus `can.<module>.<action>` in templates.
  - The sidebar hides anything not allowed. Owners have everything.
- **Starter data:** new pharmacies get Manager and Cashier roles (`template_key`), Cash/bKash/Nagad accounts, settings and the INV numbering. `provision_tenants` fills in what's missing for older ones.
- **Employees** (`people.Employee`, one per login): role, designation, all outlets or chosen outlets, joining date, NID.
  - Plan user limit enforced on create and on reactivation.
  - The owner can't be edited by others, and nobody can deactivate themselves.
  - Password reset is available.
- **Outlets and counters**:
  - Code suggestion, plan outlet limit, and you can't deactivate the last active outlet.
  - The current outlet is kept per session (`request.outlet`), with a top-bar switcher limited to allowed outlets.
- **Payment accounts:** cash, bank, mobile banking or card, with opening balance. At least one must stay active.
- **Business settings:**
  - profile, drug license no., BIN/VAT, logo (1 MB max)
  - currency symbol, position and decimals; date format
  - invoice prefix, next number and digits; invoice footer
  - `{% money %}` and `{% shop_date %}` tags; `setup.services.next_code()` for document numbers
- **Owner dashboard** has a "Set up your shop" checklist.
- **Subscription page** is limited to staff with `subscription.show`. Others see a plain notice when the account is locked.
- 124 tests.

Deferred: "default customer" moves to Phase 5, when customers exist.

### Phase 3 — Medicine master
- Categories, generics, manufacturers/brands, units with conversion (Box → Strip → Piece), racks.
- Medicine: name, alternative name, barcode, generic, strength, dosage form, prescription-required flag,
  expiry tracking, purchase/retail/wholesale price, margin, alert quantity, image.
- **Shared Bangladeshi medicine catalog** (platform-level): tenants search it and adopt medicines into their own list.
- Excel bulk import, bulk price update, opening-stock import, and barcode labels.
- Done when: a tenant imports 20k+ medicines and search by name, generic or barcode is fast.

### Phase 4 — Stock engine, suppliers and purchases
- Stock ledger and per-batch balances, average and last purchase cost, negative-stock guard.
- Suppliers with opening balance, supplier ledger and payments.
- Purchases with batch, expiry, quantity and cost per line, discount, partial payment and due; stock goes in when you confirm.
- Purchase returns linked to their batch.
- Done when: stock per batch and the supplier payable both match their ledgers.

### Phase 5 — POS and sales
- POS: search by barcode, name, generic or brand.
- FEFO batch pick with manual override, selling by box or by piece, retail or wholesale price.
- Line and cart discount, VAT.
- Expired-stock block and a prescription warning.
- Quick-add customer with credit limit, split payment, due sale, hold/recall, cash register open/close and Z-report, keyboard shortcuts.
- Print on 58mm or 80mm thermal, or A4.
- Sale list, sale returns into the same batch, customer due collection, customer ledger, promotions, quotations.
- Done when: a full sale, including a due sale, leaves stock, cash and the customer's balance consistent.

### Phase 6 — Inventory operations and expiry control
- Stock page with per-batch breakdown and stock value.
- Low-stock and expiring-soon (30/60/90 days) reports.
- Damage and expired write-offs, and returns of expired stock to suppliers.
- Transfers between outlets (dispatch → receive), and stock count/adjustment.
- Done when: near-expiry stock is visible and actionable, and transfers balance on both sides.

### Phase 7 — Accounting
- Chart of accounts per tenant, with journals posted automatically from purchases, sales, returns, payments and write-offs.
- Income and expense with categories, deposits and withdrawals.
- Account balance and statement, transaction history, trial balance, balance sheet, P&L, cash flow.
- Done when: the trial balance balances and cash in the books matches the register.

### Phase 8 — Reports and dashboard
- Reports, each with date, outlet and filter options, exportable to Excel, PDF or print:
  - Sales: daily summary, sale, detailed sale, product sale, due sales.
  - Profit: product profit, price history.
  - Stock: purchase, stock, item tracking.
  - Ledgers: supplier and customer ledger and balance.
  - Staff: employee sales.
  - Register and Z-report.
- Dashboard with KPIs, revenue chart, top sellers, recent transactions, due customers, supplier payables, and expiry alerts.

### Phase 9 — HR and payroll
- Employees, attendance (FullCalendar), salary and payroll, advance payments, sales commission.

### Phase 10 — Notifications, marketing and loyalty
- SMS, email and WhatsApp messages: invoice after sale, daily low-stock and expiry alerts, due reminders.
- Loyalty points and bulk campaigns.
- Per-tenant sender settings, or platform SMS credits.

### Phase 11 — Settings polish, printing, Bangla
- Invoice designer settings (fields, labels, logo/letterhead, terms, QR code), printer and POS settings.
- Full Bangla translation, and per-tenant data export/backup.

### Phase 12 — Production deploy
- Docker Compose (web, PostgreSQL, Redis, Celery, Nginx) and HTTPS.
- Optional subdomain per pharmacy.
- Scheduled backups, monitoring, security review, and a load test of POS search.

### Later (optional)
- Online subscription billing (bKash / SSLCommerz).
- eCommerce storefront per pharmacy.
- Installment sales.
- Offline-capable POS (PWA).

## Deliberately dropped from the demo
IMEI/serial tracking, warranty and servicing, combo products, Saudi-style e-invoice, and plugin/license/update screens.
