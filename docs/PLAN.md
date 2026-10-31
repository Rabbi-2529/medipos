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

Deferred: "default customer" moves to Phase 5, when customers exist (resolved there: no-customer sales are walk-in sales).

### Phase 3 — Medicine master ✅
- **Setup lists** (`items` app): categories, generics, manufacturers, units and racks, each unique per pharmacy.
  - One generic screen covers all five, with server-side lists and Select2 search endpoints at `/medicines/lookup/<kind>/`.
  - New pharmacies get 10 units and 7 categories.
- **Medicine** model:
  - Type (medicine or general item), brand, strength, dosage form, generic, manufacturer, category, rack, alternative name.
  - Code (auto `000001`) and barcode (unique per pharmacy).
  - Sale unit, purchase unit and conversion (e.g. Box of 100), plus pieces per strip.
  - Purchase price (4 decimals), MRP and wholesale price per sale unit; low-stock alert level.
  - Prescription-required flag, batch/expiry tracking flag, image, notes.
  - A lowercase `search_text` field: every word typed must match.
- **Medicine form:** generic, manufacturer and category are picked by name (typing a new one creates it). Shows margin, per-box prices and a live barcode.
- **Import:**
  - Excel/CSV upload, then a checked preview (row-level errors), then confirm.
  - Understands common column names (MRP, Brand, Company, Tab/Cap…).
  - Update-by-code mode; export → edit → re-import round trip; downloadable template.
  - Plan medicine limit enforced on every path.
- **Shared catalog** (`catalog.CatalogMedicine`, platform-wide):
  - The console page has import (upsert by brand + strength + form + maker), list and edit.
  - Pharmacies "Add from catalog" (ticked items, or all matching a filter). Already-added entries are skipped.
- **Bulk price update:** filter, then increase/decrease % or set margin %, with rounding that never moves a price the wrong way. Preview before applying.
- **Barcode labels:** an A4 sheet (5 × 13) with shop name, medicine, barcode (code when no barcode) and MRP.
- **Benchmark** (SQLite, dev laptop), 20,000 medicines:

  | Step | Time |
  |---|---|
  | Checking the Excel file | 7.6 s |
  | Saving | 4.8 s |
  | Search | ~20–30 ms |
  | Catalog import | 7.6 s |
  | Adopting all 20k | 5.2 s |

- 150 tests.

Moved to Phase 4: **opening-stock import** (needs the stock engine).

**Catalog data:** the repo ships only a 28-row sample (brands, generics and makers, no prices). Load the real list as platform admin under Medicine catalog → Import. Use a source ThingBD may use, such as DGDA's published registered-drug list or a licensed dataset. Don't copy other vendors' databases.

### Phase 4 — Stock engine, suppliers and purchases ✅
- **Stock engine** (`stock` app):
  - `Batch` (medicine + batch no. + expiry, with its own unit cost).
  - `BatchStock` (quantity per outlet × batch; a database constraint keeps it ≥ 0).
  - `StockMovement`, an append-only ledger with kind, signed quantity, unit cost, balance after, reference and user.
  - `stock.services.post_movement()` is the **only** way stock changes: it locks the row, refuses to go negative and writes the movement.
  - `available_batches()` gives earliest-expiry-first order for the POS.
- **Screens:**
  - Current stock: per outlet or all allowed outlets, with summary values at cost and at MRP, and nearest expiry (red within 90 days).
  - Stock card: batches in stock plus movement history.
- **Opening stock import:** the template is pre-filled with your medicine list; matching is by code, then barcode, then name. Packs + loose quantity, flexible expiry formats (2027-02, 02/2027, Feb-27), checked preview.
- **Suppliers:**
  - Ledger (credit = owed, debit = paid/returned) with an opening balance line and running balance.
  - Payments (`SPY-`) on account or against a purchase; you can't pay more than is due.
  - List shows "you owe".
- **Purchases** (`PUR-`):
  - Draft → receive; edit or delete drafts only.
  - Lines: batch, expiry (required when tracked, must be in the future), boxes + loose + **free/bonus** units, cost per box, MRP.
  - Server-side totals and discount.
  - Receiving posts stock (free units lower the batch's unit cost), credits the supplier, and optionally updates the medicine's MRP and last cost.
  - "Pay now" on receive.
  - Staff only see and receive into their allowed outlets.
- **Purchase returns** (`PRT-`): limited by what's still returnable and what's actually in stock. Stock goes out of the original batch and the supplier balance is reduced.
- 179 tests.

Later: low-stock / expiring reports, adjustments, transfers and damage (Phase 6); journals for payments and purchases (Phase 7); cash refunds from suppliers (Phase 7).

### Phase 5 — POS and sales (+ language switching) ✅
- **Language switching (user request, 2026-10-31):**
  - English ⇄ বাংলা switcher in the top bar and on the login/sign-up pages. A logged-in user's choice is saved on their account (`User.language`); visitors get a cookie.
  - Each pharmacy's business settings set the default language for staff who haven't picked one.
  - Every screen built so far is translated (1,094 strings in `locale/bn`). Medicine names stay as entered.
  - `manage.py translations extract|compile|stats` uses Babel, so no GNU gettext is needed on Windows.
  - Optional Bangla digits on receipts.
- **Cash registers:** open with a float per outlet and user; the POS asks for one first. The Z-report shows sales, discounts, VAT, due given, returns, money by account and expected cash; close it by entering the counted cash. Managers see all registers, others only their own.
- **POS** (`/sales/pos/`), a full-screen page with its own slim bar (back to dashboard, register, sales, close register, outlet, language, full screen):
  - Before any search it shows best sellers (last 90 days in the outlet, topped up with what's on the shelf). Buttons switch to a dosage form (Tablet, Capsule, Syrup…), a category, or all medicines (user request, 2026-10-31).
  - Search by name, generic, code or barcode; an exact scan adds straight to the cart.
  - Sell by piece, strip or box at retail (MRP) or wholesale price.
  - FEFO batch pick with a manual batch override. Expired batches are never sold; stock is checked before payment.
  - Line and cart discount (amount or %), VAT %, and rounding to whole taka (both in settings).
  - Customer search with quick-add (name, phone, credit limit); a customer's default discount applies automatically.
  - Split payment across the pharmacy's payment accounts (Cash, bKash, Nagad, bank); change is shown for cash.
  - Due sales need a customer and respect their credit limit. A prescription confirmation is required for Rx medicines.
  - Hold/recall carts. Shortcuts: F2 search, F4 pay, F8 hold, F9 held carts.
  - Receipts on 58 mm or 80 mm thermal paper, or an A4 invoice.
- **Sales** (`INV` numbers from the invoice sequence): list with outlet, date and due filters, plus a detail page with batches, payments and returns.
- **Sale returns** (`SRT-`): limited to what's still returnable. Stock goes back to the batches it came from, at the price actually paid after discounts. Walk-in returns are refunded in full; a customer's return first reduces their due, and any cash refund is recorded.
- **Customers:** list with balances, detail page with a running ledger (debit = owes), opening due, credit limit and default discount. Due collection (`RCV-`) on account or against a sale.
- **Default customer** (deferred from Phase 2): not needed. A sale with no customer is a walk-in sale.
- Demo seed: two customers, an open register and four sales, including a due sale with a return.
- 204 tests.

Moved to Phase 6: promotions and quotations.

### Phase 6 — Inventory operations and expiry control ✅
- **Calendar:** every date and month field uses a themed calendar (flatpickr) instead of the browser's own: the pharmacy's date format, Today/Clear buttons, Bangla month names, and a month grid for purchase expiry (past months blocked). The server still receives `YYYY-MM-DD` / `YYYY-MM`. User request, 2026-10-31.
- **Expiring & expired stock** (`/stock/expiring/`): batches by outlet, expired or within 30/60/90/180 days, with coloured "expired 20 days ago" / "in 30 days" badges. Tiles show the value at cost that is already expired, within 30, 31–60 and 61–90 days. Each row offers **Write off**, and **Return to supplier** (opens the purchase return with that batch filled in) when the batch came from a purchase. A **Write off all expired** button clears an outlet in one go. Export to Excel.
- **Low stock** (`/stock/low/`): medicines at or below their "low-stock alert at", out of stock, a suggested order (back to twice the alert, in whole packs) and the last supplier. Export for ordering.
- **Dashboard:** running low, out of stock, expiring in 90 days, expired still on the shelf, and transfers waiting to be received.
- **Damage & expired write-offs** (`DMG-`): reason (expired, damaged, lost, other), per-batch quantities, loss at cost.
- **Transfers between outlets** (`TRF-`): stock leaves the sending outlet on dispatch, sits "in transit", and arrives when the receiving outlet presses Receive. The sender can cancel while in transit (stock goes back). Only staff of the receiving outlet can receive; out and in always add up to zero.
- **Stock counts** (`ADJ-`): enter what's on the shelf per batch; the system moves stock by the difference and records found and missing value. Batches the system didn't know can be added (valued at the purchase price).
- One line editor for all three (`static/js/stock_lines.js`): pick a medicine, see its batches at the outlet with stock and expiry.
- **Promotions:** % off or an amount off each unit, for every medicine, a category, a dosage form or chosen medicines, with a minimum quantity and optional start/end dates. The POS shows the best running offer on each card and fills the line discount by itself; a typed discount replaces it. Sale lines remember the promotion.
- **Quotations** (`QT-`): a Quote button in the POS saves the cart (no stock taken) and prints an A4 quotation with a valid-until date. Quotation list (open, expired, sold) and detail; **Sell in POS** loads it back into the cart at today's prices, and the sale marks it sold.
- Permissions: transfer, damage, adjustment (stock counts), promotion and quotation; template roles updated by a data migration.
- Demo seed: expired and low stock, a received and an in-transit transfer, a write-off, a stock count, three promotions and two quotations.
- 218 tests.

Later: returning expired stock to a supplier without the original purchase, and transfer requests (ask another outlet for stock) can come with Phase 8 reports if needed.

### Phase 7 — Accounting ✅
- **Chart of accounts** per pharmacy (`apps.accounting.chart`): system accounts the app posts to (receivable, inventory, payable, VAT, owner's capital, opening balances, drawings, sales, sales returns, other income, cost of medicines sold, stock loss), one ledger account per payment account (codes 11xx), and starter expense/income categories (rent, salaries, utilities…) created in the pharmacy's language. System account names show in the reader's language; people add their own accounts (loans, furniture, new expense types).
- **Automatic journals** (`apps.accounting.posting`), one balanced entry per document, re-posted if the document changes:
  - sales (money in or customer due / sales + VAT; cost of medicines sold / stock), sale returns (with refunds), due collections, customer opening dues;
  - received purchases, purchase returns, supplier payments, supplier opening balances;
  - opening stock, write-offs, stock counts, payment accounts' opening balances.
  - `manage.py rebuild_books` re-posts everything from the documents (manual entries are kept); the demo seed ends with it.
- **Expenses and other income** (EXP-/INC-) by category, from any payment account, with outlet and reference. Categories are the pharmacy's own: **Expense categories** / **Income categories** pages (add, rename, hide) and a "+ New category" box on the entry form; each category is an account in the chart (codes assigned automatically). **Deposits, withdrawals & transfers** (FND-): move money between accounts, owner puts money in (capital), owner takes money out (drawings).
- **Cash register agrees with the books:** expenses, income and owner movements in cash are tied to the open register, so the Z-report's expected cash includes them (and shows "Expenses paid from the drawer").
- **Manual journal entries** (balanced lines, for loans, furniture, corrections) and the **journal** of every entry with links back to the document.
- **Reports:** account statement (opening, running balance, closing), trial balance, profit & loss (net sales, cost, gross margin, other income, expenses; per outlet), balance sheet (assets = liabilities + equity, profit kept in the business), cash flow (by cause and by account), payment accounts with live balances. Period filter with quick ranges; all printable.
- Money is now shown "−৳1,500.00" (minus before the currency) everywhere.
- Demo: a bank account, opening cash, this month's bills, supplier bonus, owner deposit, cash deposited at the bank, owner withdrawal.
- 230 tests (sub-ledgers match the books, cash matches the register, trial balance and balance sheet balance, rebuild gives the same books).

Simplifications: VAT on returned sales stays in sales returns; transfers between outlets don't move value between outlets (stock is one account); purchase discounts lower the inventory value of the purchase as a whole.

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
- Translate the screens added after Phase 5 into Bangla, plus Bangla invoice labels. The language switcher itself ships in Phase 5.
- Per-tenant data export/backup.

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
