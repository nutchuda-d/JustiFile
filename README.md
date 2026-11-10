# Soi Mai (ซอยไหม) – Procurement Documentation Check

ITCS380 prototype. Odoo 19 Community is the main platform; a small separate web
front end talks to it over an HTTP API.

| Who | Where | What |
|---|---|---|
| Public | http://localhost:8081 | Open view of contracts (totals, method mix, search) |
| Procurement officer | http://localhost:8081/officer.html | Record a contract, get an early reminder, submit the justification |
| Internal auditor | http://localhost:8069 (Odoo) | Import CGD data, review justifications, mark as documented / refer |
| Executive | http://localhost:8069 (Odoo) | Management View (documentation status, reasons, method mix) |

## Core flow

1. Contracts come in from the CGD open data API or are recorded by an officer on the web page.
2. Odoo groups direct-method (เฉพาะเจาะจง) contracts of the same agency + contractor inside
   30 days whose combined amount is above 500,000 THB -> **Justification Requested**.
3. The officer records the reason -> **Justification Submitted**.
4. The auditor reviews -> **Documented** or **Referred for Second Opinion**.
5. Executives see the status in the Management View.

The system points at patterns that need an explanation; it does not judge intent.

## Run it

Needs Docker Desktop.

```
copy .env.example .env          (then put your CGD_API_KEY in .env)
docker compose up -d db
docker compose run --rm odoo odoo -d soimai -i soi_mai --stop-after-init     (first time only, ~1-2 min)
docker compose up -d
```

After changing module code:

```
docker compose run --rm odoo odoo -d soimai -u soi_mai --stop-after-init
docker compose restart odoo
```

Start again from an empty database: `docker compose down -v`, then the first-time steps.

## Demo accounts (prototype only – change before any real use)

| Login | Password | Role |
|---|---|---|
| admin | admin | Odoo administrator |
| officer | officer | Procurement Officer |
| auditor | auditor | Internal Auditor |
| executive | executive | Executive (view only) |

## Getting real data from CGD

Odoo > Soi Mai > Operations > **Import from CGD Open Data** (auditor or admin).
Give the budget year (B.E.) and the agency code (`dept_code`). The key is read from
`CGD_API_KEY` in `.env`. Source: https://opend.data.go.th/govspending/service/egp-contract

The scheduled action "Soi Mai: import contracts from CGD open data" is off by default;
set the system parameter `soi_mai.cgd_dept_code` and activate it to import daily.

## Rules (Settings > Technical > System Parameters, developer mode)

| Key | Default | Meaning |
|---|---|---|
| `soi_mai.threshold` | 500000 | Bidding threshold in THB |
| `soi_mai.window_days` | 30 | Days within which contracts count as related |
| `soi_mai.near_ratio` | 0.9 | "Close to threshold" starts at this share of the threshold |

## Layout

```
addons/soi_mai/   Odoo module (models, security, views, CGD import wizard, API controllers)
web/              Separate front end (plain HTML + JS, rough layout for now)
nginx/            Serves web/ and forwards /api/ to Odoo
config/odoo.conf  Odoo settings
```

## API used by the web pages

| Method | Path | For |
|---|---|---|
| GET | /api/soi_mai/public/summary | public totals |
| GET | /api/soi_mai/public/contracts?q=&limit=&offset= | public contract search |
| POST | /api/soi_mai/officer/precheck | early reminder before saving |
| POST | /api/soi_mai/officer/contracts | record a contract |
| GET | /api/soi_mai/officer/groups | justification requests |
| POST | /api/soi_mai/officer/groups/<id>/justify | submit a justification |

Officer endpoints need an Odoo session (the page signs in through `/web/session/authenticate`).
