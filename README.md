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
| `owner@lazzpharma.local` | "Lazz Pharma (Demo)", paid on Business |
| `owner@greenlife.local` | "Green Life Pharmacy (Demo)", trial ending in 2 days |
| `owner@sunrise.local` | "Sunrise Pharmacy (Demo)", trial expired (locked out) |

All of them use the `DEMO_PASSWORD` from `.env`. New pharmacies sign up at `/signup/`, or the platform admin adds them under **Pharmacies → Add pharmacy**.

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
apps/outlets/      Outlet (branch) model; screens come in Phase 2
templates/         layouts, partials, pages
static/            css/app.css (theme tokens, dark mode), js/app.js
```

## Tenancy rules (read before adding models)

- Pharmacy data inherits `apps.core.models.TenantOwnedModel`. Its `objects` manager only sees the current tenant's rows.
- Web requests get the tenant from the logged-in user automatically.
- Celery tasks and management commands must wrap their work in `tenant_context(tenant)`.
- Use `bypass_tenant()` or `Model.all_objects` only in platform-admin code.
