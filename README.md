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
| `admin@medipos.local` | Platform admin (manages pharmacies) |
| `owner@lazzpharma.local` | Owner of "Lazz Pharma (Demo)" |
| `owner@greenlife.local` | Owner of "Green Life Pharmacy (Demo)" |

All three use the `DEMO_PASSWORD` from `.env`.

## Tests

```bash
venv/Scripts/python.exe manage.py test --settings=config.settings.test
```

## Layout

```
config/            settings (base/dev/prod/test), urls
apps/core/         tenancy (context, TenantOwnedModel, middleware), menu registry, dashboard
apps/tenants/      Tenant (pharmacy) model, seed_demo command
apps/accounts/     custom User (email/phone login), auth views
templates/         layouts, partials, pages
static/            css/app.css (theme tokens, dark mode), js/app.js
```

## Tenancy rules (read before adding models)

- Pharmacy data inherits `apps.core.models.TenantOwnedModel`. Its `objects` manager only sees the current tenant's rows.
- Web requests get the tenant from the logged-in user automatically.
- Celery tasks and management commands must wrap their work in `tenant_context(tenant)`.
- Use `bypass_tenant()` or `Model.all_objects` only in platform-admin code.
