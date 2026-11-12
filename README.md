# JustiFile – Audit-Ready Procurement Documentation

ITCS380 prototype. Odoo 19 Community is the main platform; a small separate web
front end talks to it over an HTTP API.

| Who | Where | What |
|---|---|---|
| Public | http://localhost:8081 | Open view of contracts (totals, method mix, search) |
| Procurement officer | http://localhost:8081/officer.html | Record a contract, get an early reminder, submit the justification |
| Internal auditor | http://localhost:8069 (Odoo) | Import CGD data, review justifications, mark as documented / refer |
| Executive | http://localhost:8069 (Odoo) | Management View > Dashboard (5 KPIs on one page, click to drill down) |

## Core flow

1. Contracts come in from the CGD open data API or are recorded by an officer on the web page.
2. Odoo groups direct-method (เฉพาะเจาะจง) contracts of the same agency + contractor inside
   30 days whose combined amount is above 500,000 THB -> **Justification Requested**.
3. The officer records the reason -> **Justification Submitted**.
4. The auditor reviews -> **Documented** or **Referred for Second Opinion**.
5. Executives see the status in the Management View.

The system points at patterns that need an explanation; it does not judge intent.

## Features

- **Management Dashboard** (Odoo): documentation completeness, waiting requests, average days to
  submit, direct-method share, contracts close to the threshold; each card opens the records behind it.
- **Reason at the time of saving**: only when a new contract, together with related ones, goes over
  the threshold, the officer page asks for the reason right in the form (or "add the reason later").
  The officer can point to the existing purchase request report instead of retyping it. Ordinary
  contracts are saved without any extra question.
- **Compare years**: the dashboard has a budget-year selector; each KPI shows the change from the
  previous year, and a "Year by year" table lists every year. Management View > Year Comparison is a
  pivot of years × procurement method.
- **Contracts by amount** (dashboard): number of contracts per 50,000 THB band around the threshold.
- **Two-way accountability**: an auditor must write a note before referring a group back.
- **Supporting documents**: the officer attaches PDF/PNG/JPG files (up to 5, 5 MB each) with the
  justification; the auditor sees them in the chatter of the group in Odoo.
- **Contractor history** (officer page): typing a tax ID fills in the name and shows past contracts
  (facts only, no rating).
- **Public filters**: agency, province, procurement method and budget year.

## Run it

Needs Docker Desktop.

```
copy .env.example .env          (then put your CGD_API_KEY in .env)
docker compose up -d db
docker compose run --rm odoo odoo -d justifile -i justifile --stop-after-init     (first time only, ~1-2 min)
docker compose up -d
```

After changing module code:

```
docker compose run --rm odoo odoo -d justifile -u justifile --stop-after-init
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

Odoo > JustiFile > Operations > **Import from CGD Open Data** (auditor or admin).
Give a range of budget years (B.E., e.g. 2566–2569; the current year is published late) and the exact **Agency Name** as written in e-GP (e.g. เทศบาลตำบลศาลายา); leave Max Project Budget at 0. The API has no agency code in its data, so the import searches by name and keeps only exact matches. The key is read from
`CGD_API_KEY` in `.env`. Source: https://opend.data.go.th/govspending/service/egp-contract

The scheduled action "JustiFile: import contracts from CGD open data" is off by default;
set the system parameter `justifile.cgd_agency_name` (e.g. เทศบาลตำบลศาลายา) and activate it to import daily.

## Rules (Settings > Technical > System Parameters, developer mode)

| Key | Default | Meaning |
|---|---|---|
| `justifile.threshold` | 500000 | Bidding threshold in THB |
| `justifile.window_days` | 30 | Days within which contracts count as related |
| `justifile.near_ratio` | 0.9 | "Close to threshold" starts at this share of the threshold |

## Layout

```
addons/justifile/   Odoo module (models, security, views, CGD import wizard, API controllers)
web/              Separate front end (plain HTML + JS, rough layout for now)
nginx/            Serves web/ and forwards /api/ to Odoo
config/odoo.conf  Odoo settings
```

## API used by the web pages

| Method | Path | For |
|---|---|---|
| GET | /api/justifile/public/filters | values for the public filter boxes |
| GET | /api/justifile/public/summary?agency=&province=&method=&year=&q= | public totals |
| GET | /api/justifile/public/contracts?...&limit=&offset= | public contract search |
| GET | /api/justifile/officer/vendor?tin=&agency= | contractor name and past contracts |
| POST | /api/justifile/officer/precheck | early reminder before saving |
| POST | /api/justifile/officer/contracts | record a contract |
| GET | /api/justifile/officer/groups | justification requests |
| POST | /api/justifile/officer/groups/<id>/justify | submit a justification (+ optional files, base64) |
| GET | /api/justifile/officer/attachments/<id> | download a supporting document |

Officer endpoints need an Odoo session (the page signs in through `/web/session/authenticate`).
