# LeadFinder

**LeadFinder** is a B2B lead-generation tool for small-to-mid sales teams and freelancers: a natural-language keyword query drives a **SerpApi** search, concurrent page scraping and searches, and extraction of business emails and phone numbers into persistent storage, surfaced through a Flask web dashboard.

## Architecture

```
SERP query (SerpAPI)
  → domain denylist filter
  → robots.txt compliance check
  → httpx page fetch with tenacity retry
  → Playwright fallback (JS-shell detection only)
  → 4-tier contact extraction:
      1. Schema.org / JSON-LD  (high confidence)
      2. mailto: / tel: links  (high confidence)
      3. Footer/header DOM zones (medium confidence)
      4. Body-text regex       (low confidence)
  → phonenumbers normalisation (E.164)
  → email normalisation + junk filter
  → cross-page deduplication
  → SQLite upsert (seen-count + provenance trail)
  → SMTP E-Mail builder and Send system
  → Flask JSON API + HTML dashboard
```

## Setup

**1. Create and activate a virtual environment**

```bash
python3 -m venv .venv           # if running manually with PIP; use 'uv sync' directly below if installed
source .venv/bin/activate       # Windows: .venv\Scripts\activate
```

**2. Install dependencies**

```bash
uv sync | pip install -r requirements.txt # uv automatically handles dependecy install and .venv creation with 'uv sync'
```

**3. (Optional) Install Playwright for JS-fallback rendering**

```bash
pip install playwright
playwright install chromium
```

**4. Configure environment**

```bash
cp .env.example .env
# Edit .env — set SERP_API_KEY to your SerpAPI key
```

Get a key at https://serpapi.com/ (250 free searches/month on the free tier).

**5. Run**

```bash
uv run python main.py | python main.py # exclude 'debug= True' from main.py for non debugging runs
# or
uv run flask --app main run --debug | flask --app main run --debug # exclude -- debug for non debugging runs

```

Open http://localhost:5000/ | https://127.0.0.1:5000/

## API

| Method | Endpoint             | Description                                         |
| ------ | -------------------- | --------------------------------------------------- |
| GET    | `/api/health`        | `{"status": "ok", "service": "leadfinder"}`         |
| GET    | `/api/queue/status`  | `{"max_workers":"3", "note": "..."}`                |
| POST   | `/api/search`        | `{"query":"...", "max_results": 10}` — run pipeline |
| GET    | `/api/leads`         | Filter: `?query=&domain=&confidence=&contact_type=` |
| GET    | `/api/leads/export`  | Same filters → CSV download                         |
| GET    | `/api/stats`         | Lead counts by type and confidence                  |
| GET    | `/api/runs`          | List of past search runs                            |
| GET    | `/api/runs/<id>`     | Single run detail                                   |
| GET    | `/api/health`        | `{"status":"ok"}`                                   |
| POST   | `/api/email/preview` | `{"previews": previews, "total_would_send":.. }`    |
| POST   | `/api/email/send`    | `{"sent":"...", "failed":"...", "results:"..." }`   |

## Dashboard

| Route           | Description                                         |
| --------------- | --------------------------------------------------- |
| `/`             | Overview — stats, confidence breakdown, recent runs |
| `/search`       | Search interface                                    |
| `/leads`        | Filterable leads table                              |
| `/leads/export` | CSV export with filter options                      |
| `/email`        | Email builder, filtr and sender using SMTP          |

## Compliance

- Respects `robots.txt` via `protego` (same parser Scrapy uses)
- Enforces `Crawl-delay` when specified
- Per-domain rate limiting via configurable `RATE_LIMIT_DELAY_SECONDS`
- Honest `User-Agent` header
- Domain denylist applied before any fetch
- Only publicly available business contact information is targeted
- No CAPTCHA bypass, no login-wall bypass, no paywalls

## Directory structure

```
leadfinder/
├── main.py
├── pyptoject.toml
├── uv.lock
├── .python_version
├── .env.example
├── app/
│   ├── __init__.py
│   ├── config.py                # application wide configuration handler
│   ├── models.py                # shared dataclasses
│   ├── logging_config.py        # per file logging handler
│   ├── scraper/
│   │   ├── fetcher.py           # httpx + tenacity
│   │   ├── robots.py            # protego robots.txt cache
│   │   ├── discovery.py         # contact-page link finder
│   │   └── playwright_renderer.py
│   ├── extraction/
│   │   ├── structured.py        # JSON-LD / schema.org
│   │   ├── patterns.py          # mailto/tel/footer/text
│   │   ├── confidence.py        # domain-match scoring
│   │   └── extractor.py         # orchestrator
│   ├── normalize/
│   │   ├── email.py
│   │   ├── phone.py             # E.164 via phonenumbers
│   │   └── dedupe.py
│   ├── storage/
│   │   ├── db.py                # schema + connection
│   │   └── repository.py        # all SQL
│   ├── email/
│   │   ├── sender.py            # Email send orchestrator
│   │   └── smtp.py              # SMTP configurations
│   ├── templates/
│   │   ├── base.html            # Base frontend template
│   │   ├── index.html           # Hompage
│   │   ├── search.html          # Search Page
│   │   ├── email.html           # Email send page
│   │   ├── leads.html           # Leads exploring page
│   │   └── export.html          # Data export page
│   ├── static/
│   │   ├── css/style.css        # CSS
│   │   ├── images/icon.png      # Frontend webpage icon
│   │   └── js/main.js           # Frontend Javascript
│   ├── services/
│   │   └── search_service.py    # Complete pipeline orchestration
│   ├── serp/client.py           # SerpAPI request client
│   ├── api/routes.py            # JSON API blueprint
│   ├── views/routes.py          # HTML dashboard rputes
│   ├── templates/
│   └── static/
│       └── playwright_renderer.py
├──tests/test_leadfinder.py      # Complete testing suite
└── data/                        # SQLite database lives here
```
