# listing-monitor

I was getting 30–40 listing alert emails a day from multiple sites. Most were irrelevant — wrong price, wrong area, wrong type. I spent 20 minutes every morning scanning them manually, opening tabs, mentally scoring each one. Most mornings I'd find maybe 2–3 worth a second look.

So I built an agent that reads the emails, extracts every listing, scores each one against my criteria using Claude, and shows me only the matches worth looking at. It runs on a Mac Mini via a daily cron job and costs under $1/month in API calls.

Now I wake up, open a dashboard, and see the 3–5 listings that actually matter — scored, sorted, with reasoning for each one.

## How it works

```
Gmail inbox                    Profile config
     │                              │
     ▼                              │
 Fetch emails (Gmail API)           │
     │                              │
     ▼                              │
 Parse HTML → extract listings      │
     │                              │
     ▼                              │
 Pre-filter (hard constraints) ◄────┘
     │                              │
     │  (rejects never hit the API) │
     ▼                              │
 Score with Claude Haiku ◄──────────┘
     │
     ▼
 SQLite database
     │
     ▼
 Local dashboard (FastAPI)
```

1. **Fetch**: Pulls listing alert emails from Gmail using the API. Tracks processed message IDs to avoid re-reading.
2. **Parse**: Each email source has its own HTML parser (BeautifulSoup). Extracts price, type, surface, location, rooms, photos, listing URL.
3. **Pre-filter**: Hard constraints (price ceiling, minimum size, rejected types) are checked locally — listings that fail never touch the API. Saves money.
4. **Score**: Surviving listings go to Claude Haiku with the full scoring profile. Returns a 0–10 score, reasoning, flags, and feature observations.
5. **Store**: Everything goes into SQLite — listings, scores, stars, notes.
6. **Dashboard**: FastAPI serves a single-page app at `localhost:8501`. Filter by score, price, date, region. Star listings, add notes.

## Dashboard

![Dashboard](docs/dashboard.png)

## What a score looks like

The scorer returns structured JSON for each listing:

```json
{
  "score": 9,
  "hard_constraint_pass": true,
  "hard_constraint_failures": [],
  "flags": [{"flag": "energy_class_F", "note": "expected at this price range"}],
  "notable_features": ["stone construction", "terrace"],
  "reasoning": "Strong price-to-size ratio in target region. Renovation-ready, which matches buyer preference for underpriced space.",
  "condition_estimate": "needs renovation"
}
```

## Setup

### 1. Gmail API credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Enable the **Gmail API**
2. Create an **OAuth 2.0 Client ID** (Desktop app)
3. Download the JSON → save as `gmail/credentials.json`
4. First run opens a browser for OAuth consent. After that, the token is saved to `gmail/token.json`

### 2. Anthropic API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Or add it to your shell profile. The venv activate script can also inject it (see `venv/bin/activate`).

### 3. Install dependencies

```bash
uv venv venv
source venv/bin/activate
uv pip install -r requirements.txt
```

### 4. Create your scoring profile

Copy the sample and adapt it to your use case:

```bash
cp config/sample-profile.json config/your-profile.json
```

The profile defines hard constraints (instant rejects), scoring tiers, target regions, accepted/rejected types, flags that need human review, and features to observe. See `config/sample-profile.json` for the full structure — it uses a "used cars under $15K" example to show every field.

### 5. Write a parser for your email source

Each listing site sends different HTML. You need one parser per source. See `parsers/example_listing_site.py` for the pattern:

- Find listing blocks in the HTML (comment markers or repeated DOM structures)
- Extract fields using BeautifulSoup (anchor `name` attributes, CSS classes, etc.)
- Return a list of dicts matching the database schema

Test standalone:
```bash
python gmail/fetch_emails.py --dump          # Save a sample email
python parsers/your_parser.py                # Test against it
```

### 6. Run it

```bash
python run.py                # Normal run (last 24 hours)
python dashboard.py          # Start the dashboard
```

## Flags

| Flag | What it does |
|---|---|
| `--days N` | Fetch emails from last N days (default: 1) |
| `--dry-run` | Everything except API scoring — no cost |
| `--rescore` | Delete all scores, re-score every listing. Use after profile changes. |
| `--dedup` | Remove duplicate listings and exit |

## Standalone scripts

```bash
python gmail/fetch_emails.py                      # List recent alert emails
python gmail/fetch_emails.py --dump               # Save most recent email HTML
python gmail/fetch_emails.py --dump-from <source> # Save most recent from a specific sender
python parsers/example_listing_site.py            # Test parser against sample
python scorer/score.py --test                     # Score a hardcoded sample listing
python dashboard.py                               # Dashboard at localhost:8501
```

## Cost

Claude Haiku is the cheapest model. Each listing costs roughly $0.001–0.002 to score (the prompt includes the full profile + listing data, ~2K tokens in, ~500 out).

At 10 new listings/day: **~$0.50/month**.

Pre-filtering rejects (wrong price, wrong type, too small) before they hit the API, so you only pay for plausible candidates.

## Stack

- Python 3.12
- Gmail API (google-api-python-client)
- BeautifulSoup + lxml (HTML parsing)
- Anthropic SDK + Claude Haiku (scoring)
- SQLite (storage)
- FastAPI + uvicorn (dashboard)
- Nominatim/OpenStreetMap (geocoding)