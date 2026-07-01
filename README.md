# LeadFinder

A Flask-based lead contact retrieval tool.  
Queries **SerpAPI**, scrapes the returned pages, and extracts publicly available business contact information (emails and phone numbers).

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
  → Flask JSON API + HTML dashboard
```

## Setup

**1. Create and activate a virtual environment**

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
```

**2. Install dependencies**

```bash
pip install -r requirements.txt
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
python run.py
# or
flask --app run run --debug
```

Open http://localhost:5000

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/search` | `{"query":"...", "max_results": 10}` — run pipeline |
| GET  | `/api/leads` | Filter: `?query=&domain=&confidence=&contact_type=` |
| GET  | `/api/leads/export` | Same filters → CSV download |
| GET  | `/api/stats` | Lead counts by type and confidence |
| GET  | `/api/runs` | List of past search runs |
| GET  | `/api/runs/<id>` | Single run detail |
| GET  | `/api/health` | `{"status":"ok"}` |

## Dashboard

| Route | Description |
|-------|-------------|
| `/` | Overview — stats, confidence breakdown, recent runs |
| `/search` | Search interface |
| `/leads` | Filterable leads table |
| `/leads/export` | CSV export with filter options |

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
├── run.py
├── requirements.txt
├── .env.example
├── app/
│   ├── config.py
│   ├── models.py            # shared dataclasses
│   ├── serp/client.py       # SerpAPI wrapper
│   ├── scraper/
│   │   ├── fetcher.py       # httpx + tenacity
│   │   ├── robots.py        # protego robots.txt cache
│   │   ├── discovery.py     # contact-page link finder
│   │   └── playwright_renderer.py
│   ├── extraction/
│   │   ├── structured.py    # JSON-LD / schema.org
│   │   ├── patterns.py      # mailto/tel/footer/text
│   │   ├── confidence.py    # domain-match scoring
│   │   └── extractor.py     # orchestrator
│   ├── normalize/
│   │   ├── email.py
│   │   ├── phone.py         # E.164 via phonenumbers
│   │   └── dedupe.py
│   ├── storage/
│   │   ├── db.py            # schema + connection
│   │   └── repository.py    # all SQL
│   ├── services/
│   │   └── search_service.py  # pipeline orchestration
│   ├── api/routes.py        # JSON API blueprint
│   ├── views/routes.py      # HTML dashboard blueprint
│   ├── templates/
│   └── static/
└── data/                    # SQLite database lives here
```
