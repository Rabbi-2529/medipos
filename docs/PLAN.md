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

### Phase 1 — SaaS platform and onboarding
- Pharmacy self-signup: business, owner and first outlet, starting a trial.
- Plans with limits (outlets, users, medicines) and subscription periods.
- Trial expiry and suspension gate: a suspended pharmacy can log in only to see a billing notice.
- Platform console: pharmacy list, details, activate/suspend, extend subscription, record a manual payment, usage counts.
- Done when: a new pharmacy signs up, works during the trial, is suspended at expiry, and is reactivated by the platform admin.

### Phase 2 — Shop setup and access
- Business settings: name, logo, currency, date format, invoice numbering, default customer and payment method.
- Outlets, "enter outlet", and allowed outlets per user.
- Roles and permissions: module × list/create/edit/delete/show grid, driven by the menu registry `key`s.
- Employees, counters (tills), payment accounts (Cash, Bank, bKash, Nagad).
- Done when: an owner creates a cashier role, and the cashier sees only allowed menus and outlets.

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
