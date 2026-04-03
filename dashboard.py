import json
import os
import re
import sqlite3
import unicodedata
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
import uvicorn


def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

from storage.database import get_connection, init_db
from geo.locate import get_geo_info

app = FastAPI()

PORT = 8501


def _ensure_dashboard_columns():
    """Add starred and notes columns to scores table if they don't exist."""
    conn = get_connection()
    cursor = conn.execute("PRAGMA table_info(scores)")
    columns = {row["name"] for row in cursor.fetchall()}
    if "starred" not in columns:
        conn.execute("ALTER TABLE scores ADD COLUMN starred BOOLEAN DEFAULT 0")
    if "notes" not in columns:
        conn.execute("ALTER TABLE scores ADD COLUMN notes TEXT DEFAULT NULL")
    conn.commit()
    conn.close()


@app.on_event("startup")
def startup():
    init_db()
    _ensure_dashboard_columns()


DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_PAGE


@app.get("/static/{filename}")
def static_file(filename: str):
    path = os.path.join(DOCS_DIR, filename)
    if os.path.exists(path):
        return FileResponse(path)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/listings")
def api_listings(
    min_score: int = Query(0),
    days: int = Query(0),
    min_price: int = Query(0),
    max_price: int = Query(999999),
    region: str = Query(""),
    starred_only: bool = Query(False),
    has_notes: bool = Query(False),
):
    conn = get_connection()

    where = ["s.score IS NOT NULL"]
    params = []

    if min_score > 0:
        where.append("s.score >= ?")
        params.append(min_score)

    if days > 0:
        where.append(f"s.date_scored >= datetime('now', '-{int(days)} day')")

    if min_price > 0:
        where.append("(l.price_eur IS NULL OR l.price_eur >= ?)")
        params.append(min_price)

    if max_price < 999999:
        where.append("(l.price_eur IS NULL OR l.price_eur <= ?)")
        params.append(max_price)

    region_filter = strip_accents(region.lower()) if region else ""

    if starred_only:
        where.append("s.starred = 1")

    if has_notes:
        where.append("s.notes IS NOT NULL AND s.notes != ''")

    where_sql = " AND ".join(where)

    rows = conn.execute(
        f"""SELECT l.id, l.source, l.url, l.title, l.price_eur, l.location,
                   l.bedrooms, l.rooms, l.surface_m2, l.land_m2, l.property_type,
                   l.description, l.photo_urls, l.energy_class, l.date_found,
                   s.score, s.hard_constraint_pass, s.flags, s.notable_features,
                   s.reasoning, s.condition_estimate, s.date_scored,
                   s.starred, s.notes
            FROM listings l
            JOIN scores s ON s.id = (
                SELECT s2.id FROM scores s2
                WHERE s2.listing_id = l.id
                ORDER BY s2.id DESC LIMIT 1
            )
            WHERE {where_sql}
            ORDER BY COALESCE(s.starred, 0) DESC, s.score DESC, s.date_scored DESC""",
        params,
    ).fetchall()
    conn.close()

    results = []
    for r in rows:
        d = dict(r)
        for json_field in ("flags", "notable_features", "photo_urls"):
            if d.get(json_field) and isinstance(d[json_field], str):
                try:
                    d[json_field] = json.loads(d[json_field])
                except json.JSONDecodeError:
                    pass
        d["starred"] = bool(d.get("starred"))
        loc = d.get("location")
        if region_filter:
            loc_normalized = strip_accents((loc or "").lower())
            if re.match(r'^\d{1,2}$', region_filter):
                if not re.search(r'\(' + region_filter + r'\d', loc_normalized):
                    continue
            elif region_filter not in loc_normalized:
                continue
        if loc:
            geo = get_geo_info(loc)
            if geo:
                d["nearest_city"] = geo["nearest_city"]
                d["city_distance_km"] = geo["city_distance_km"]
                d["coast_distance_km"] = geo["coast_distance_km"]
        results.append(d)

    return results


@app.post("/api/listings/{listing_id}/star")
def toggle_star(listing_id: int):
    conn = get_connection()
    current = conn.execute(
        "SELECT starred FROM scores WHERE listing_id = ?", (listing_id,)
    ).fetchone()
    if not current:
        conn.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    new_val = 0 if current["starred"] else 1
    conn.execute(
        "UPDATE scores SET starred = ? WHERE listing_id = ?", (new_val, listing_id)
    )
    conn.commit()
    conn.close()
    return {"listing_id": listing_id, "starred": bool(new_val)}


@app.post("/api/listings/{listing_id}/notes")
def save_notes(listing_id: int, body: dict):
    conn = get_connection()
    conn.execute(
        "UPDATE scores SET notes = ? WHERE listing_id = ?",
        (body.get("notes", ""), listing_id),
    )
    conn.commit()
    conn.close()
    return {"listing_id": listing_id, "notes": body.get("notes", "")}


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>listing-monitor</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Cpath d='M32 6L4 30h8v24h16V38h8v16h16V30h8L32 6z' fill='%231565c0'/%3E%3Cpath d='M32 6L4 30h8v24h16V38h8v16h16V30h8L32 6z' fill='none' stroke='%230d47a1' stroke-width='2'/%3E%3Crect x='22' y='20' width='8' height='8' rx='1' fill='%2390caf9'/%3E%3Crect x='34' y='20' width='8' height='8' rx='1' fill='%2390caf9'/%3E%3C/svg%3E">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f0f0f0; color: #222; }

.header { background: #1a1a2e; color: #fff; padding: 12px 24px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
.header h1 { font-size: 18px; font-weight: 600; }
.header .port { font-size: 12px; color: #888; margin-left: 8px; }
.header nav a { color: #556; font-size: 12px; margin-left: 12px; text-decoration: none; }
.header nav a:hover { color: #aaa; }
.header nav a.active { color: #6cf; }

.filters { background: #fff; padding: 16px 24px; border-bottom: 1px solid #ddd; display: flex; flex-wrap: wrap; gap: 16px; align-items: center; }
.filter-group { display: flex; flex-direction: column; gap: 4px; }
.filter-group label { font-size: 11px; font-weight: 600; text-transform: uppercase; color: #666; }
.filter-group select, .filter-group input { padding: 6px 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 13px; }
.filter-group input[type=text] { width: 140px; }
.filter-group input[type=number] { width: 90px; }
.filter-group .checkbox-wrap { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
.filter-group .checkbox-wrap input { width: auto; }

.stats { padding: 8px 24px; font-size: 13px; color: #666; background: #fafafa; border-bottom: 1px solid #eee; }

.cards { max-width: 960px; margin: 0 auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }

.card { background: #fff; border-radius: 10px; padding: 16px 20px; border: 2px solid #e8e8e8; transition: border-color 0.2s; display: flex; gap: 16px; }
.card.starred { border-color: #d4a017; }
.card-body { flex: 1; min-width: 0; }
.card-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
.card-main { flex: 1; }

.card-thumb { flex-shrink: 0; width: 120px; }
.card-thumb img { width: 120px; border-radius: 8px; object-fit: cover; aspect-ratio: 4/3; background: #eee; }
.card-thumb .placeholder { width: 120px; height: 90px; border-radius: 8px; background: #e0e0e0; display: flex; align-items: center; justify-content: center; }
.card-thumb .placeholder svg { width: 32px; height: 32px; opacity: 0.4; }

.badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 13px; font-weight: 700; color: #fff; min-width: 36px; text-align: center; }
.badge.high { background: #2e7d32; }
.badge.mid { background: #1565c0; }
.badge.low { background: #888; }
.badge.zero { background: #c62828; }

.card h3 { font-size: 15px; margin: 4px 0; }
.card .meta { font-size: 13px; color: #555; line-height: 1.6; }
.card .meta span { margin-right: 14px; }
.card .reasoning { font-size: 13px; color: #333; margin-top: 8px; line-height: 1.5; }
.card .condition { font-size: 12px; color: #6a1b9a; margin-top: 4px; }

.flags { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.flag { background: #fff8e1; border: 1px solid #ffe082; border-radius: 4px; padding: 2px 8px; font-size: 11px; color: #6d4c00; }

.features { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px; }
.feature { background: #e8f5e9; border-radius: 4px; padding: 2px 8px; font-size: 11px; color: #2e7d32; }

.card-actions { display: flex; gap: 8px; align-items: flex-start; margin-top: 8px; padding-top: 8px; border-top: 1px solid #f0f0f0; }
.star-btn { background: none; border: 1px solid #ccc; border-radius: 6px; padding: 4px 10px; cursor: pointer; font-size: 16px; }
.star-btn.active { border-color: #d4a017; background: #fff8e1; }
.notes-input { flex: 1; padding: 6px 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 12px; font-family: inherit; }
.save-btn { background: #1565c0; color: #fff; border: none; border-radius: 6px; padding: 6px 12px; cursor: pointer; font-size: 12px; }
.save-btn:hover { background: #0d47a1; }
.title-link { color: #1565c0; text-decoration: none; }
.title-link:hover { text-decoration: underline; }
.geo-context { font-size: 12px; color: #888; }

.dept-bar { padding: 8px 24px; background: #fff; border-bottom: 1px solid #eee; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.dept-bar .dept-label { font-size: 11px; font-weight: 600; text-transform: uppercase; color: #666; margin-right: 4px; }
.dept-chip { background: #f0f0f0; border: 1px solid #ddd; border-radius: 12px; padding: 3px 10px; font-size: 12px; color: #444; cursor: pointer; transition: all 0.15s; }
.dept-chip:hover { background: #e3f2fd; border-color: #90caf9; }
.dept-chip.active { background: #1565c0; color: #fff; border-color: #1565c0; }

.map-toggle { background: none; border: 1px solid #ccc; border-radius: 12px; padding: 3px 10px; font-size: 12px; color: #666; cursor: pointer; margin-left: auto; }
.map-toggle:hover { background: #f5f5f5; }
.map-modal { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 200; }
.map-modal img { max-width: 90vw; max-height: 85vh; border-radius: 8px; box-shadow: 0 8px 32px rgba(0,0,0,0.3); border: 4px solid #fff; }
.map-modal .close-btn { position: absolute; top: 16px; right: 24px; background: #fff; border: none; border-radius: 50%; width: 36px; height: 36px; font-size: 20px; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.2); color: #333; line-height: 36px; text-align: center; }
.map-modal .close-btn:hover { background: #eee; }

.cmd-card { background: #1e1e2e; border-radius: 12px; padding: 24px 32px; max-width: 900px; width: 90vw; box-shadow: 0 8px 32px rgba(0,0,0,0.4); position: relative; }
.cmd-card h3 { color: #ccc; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; margin: 16px 0 8px 0; }
.cmd-card h3:first-child { margin-top: 0; }
.cmd-card pre { margin: 0; font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 12px; line-height: 1.8; color: #a0a0a0; }
.cmd-card .cmd { color: #e0e0e0; }
.cmd-card .arr { color: #555; }
.cmd-card .desc { color: #888; }

.card-price { font-size: 22px; font-weight: 800; color: #1a1a1a; margin-bottom: 2px; }
.card-price .source-tag { vertical-align: middle; }

.source-tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-left: 6px; vertical-align: middle; }
.source-tag.seloger { background: #fde8e8; color: #c62828; }
.source-tag.notaires { background: #e3f2fd; color: #1565c0; }
.source-tag.leboncoin { background: #fff3e0; color: #e65100; }
.source-tag.other { background: #f5f5f5; color: #666; }

@media (max-width: 640px) {
  .filters { padding: 12px; gap: 10px; }
  .cards { padding: 10px; }
  .card { padding: 12px; flex-direction: column; }
  .card-thumb { width: 100%; }
  .card-thumb img { width: 100%; max-height: 180px; }
  .card-thumb .placeholder { width: 100%; }
  .card-top { flex-direction: column; }
}
</style>
</head>
<body>

<div class="header">
  <div style="display:flex;align-items:center;">
    <h1>listing-monitor</h1>
    <span class="port">:8501</span>
  </div>
  <nav>
    <a href="#" class="active">:8501 listings</a>
    <a href="#">:8502</a>
    <a href="#">:8503</a>
    <a href="#">:8504</a>
    <a href="#">:8505</a>
  </nav>
</div>

<div class="filters">
  <div class="filter-group">
    <label>Date Range</label>
    <select id="f-days">
      <option value="7">Last 7 days</option>
      <option value="30">Last 30 days</option>
      <option value="0" selected>All time</option>
    </select>
  </div>
  <div class="filter-group">
    <label>Min Score</label>
    <select id="f-score">
      <option value="0">All</option>
      <option value="5">5+</option>
      <option value="7" selected>7+</option>
      <option value="9">9+</option>
    </select>
  </div>
  <div class="filter-group">
    <label>Price Min</label>
    <input type="number" id="f-min-price" value="0" step="1000">
  </div>
  <div class="filter-group">
    <label>Price Max</label>
    <input type="number" id="f-max-price" value="50000" step="1000">
  </div>
  <div class="filter-group">
    <label>Region</label>
    <input type="text" id="f-region" placeholder="e.g. region name">
  </div>
  <div class="filter-group">
    <label>Filter</label>
    <div class="checkbox-wrap">
      <input type="checkbox" id="f-starred">
      <label for="f-starred" style="font-size:13px;text-transform:none;color:#333;">Starred only</label>
    </div>
    <div class="checkbox-wrap">
      <input type="checkbox" id="f-notes">
      <label for="f-notes" style="font-size:13px;text-transform:none;color:#333;">Has notes</label>
    </div>
  </div>
</div>

<div class="dept-bar">
  <span class="dept-label">Dept:</span>
  <span id="dept-chips"></span>
  <button class="map-toggle" id="map-toggle">Show map</button>
  <button class="map-toggle" id="cmd-toggle">Commands</button>
</div>

<div class="stats" id="stats"></div>
<div class="cards" id="cards"></div>

<script>
// Add departments here as you discover new areas of interest.
var DEPARTMENTS = [
  {"code": "11", "name": "Aude"},
  {"code": "30", "name": "Gard"},
  {"code": "34", "name": "H\u00e9rault"},
  {"code": "66", "name": "Pyr\u00e9n\u00e9es-Orientales"},
  {"code": "09", "name": "Ari\u00e8ge"},
  {"code": "12", "name": "Aveyron"},
  {"code": "81", "name": "Tarn"},
  {"code": "48", "name": "Loz\u00e8re"},
];

var activeDept = null;

function stripAccents(s) {
  return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
}

function renderDeptChips() {
  document.getElementById('dept-chips').innerHTML = DEPARTMENTS.map(function(d) {
    return '<span class="dept-chip' + (activeDept === d.code ? ' active' : '') + '" data-code="' + d.code + '">' + d.code + ' ' + d.name + '</span>';
  }).join('');
  document.querySelectorAll('.dept-chip').forEach(function(el) {
    el.addEventListener('click', function() {
      var code = this.dataset.code;
      if (activeDept === code) {
        activeDept = null;
        document.getElementById('f-region').value = '';
      } else {
        activeDept = code;
        document.getElementById('f-region').value = code;
      }
      renderDeptChips();
      loadListings();
    });
  });
}

document.getElementById('map-toggle').addEventListener('click', function() {
  var modal = document.createElement('div');
  modal.className = 'map-modal';
  modal.innerHTML = '<button class="close-btn">\u00d7</button><img src="/static/france-departements.jpg" alt="Department reference map">';
  function closeModal() { if (modal.parentNode) modal.parentNode.removeChild(modal); }
  modal.addEventListener('click', function(e) { if (e.target === modal) closeModal(); });
  modal.querySelector('.close-btn').addEventListener('click', closeModal);
  document.addEventListener('keydown', function handler(e) { if (e.key === 'Escape') { closeModal(); document.removeEventListener('keydown', handler); } });
  document.body.appendChild(modal);
});

document.getElementById('cmd-toggle').addEventListener('click', function() {
  var modal = document.createElement('div');
  modal.className = 'map-modal';
  modal.innerHTML = '<div class="cmd-card">' +
    '<button class="close-btn" style="position:absolute;top:8px;right:8px;background:#333;color:#aaa;border:none;border-radius:50%;width:28px;height:28px;font-size:16px;cursor:pointer;line-height:28px;text-align:center;">\u00d7</button>' +
    '<h3>Pipeline commands</h3>' +
    '<pre>' +
    '<span class="cmd">python run.py</span>                    <span class="arr">\u2192</span> <span class="desc">Fetch new emails, score listings, results appear on dashboard</span>\\n' +
    '<span class="cmd">python run.py --days 7</span>           <span class="arr">\u2192</span> <span class="desc">Same but covers the last 7 days of emails</span>\\n' +
    '<span class="cmd">python run.py --dry-run --days 3</span> <span class="arr">\u2192</span> <span class="desc">Fetch and parse only, no scoring. Prints listing count in terminal</span>\\n' +
    '<span class="cmd">python run.py --rescore</span>          <span class="arr">\u2192</span> <span class="desc">Delete all scores and re-score every listing. Use after editing profile</span>\\n' +
    '<span class="cmd">python run.py --dedup</span>            <span class="arr">\u2192</span> <span class="desc">Find and remove duplicate listings from the database</span>' +
    '</pre>' +
    '<h3>Standalone</h3>' +
    '<pre>' +
    '<span class="cmd">python gmail/fetch_emails.py</span>                      <span class="arr">\u2192</span> <span class="desc">Print list of recent alert emails in terminal</span>\\n' +
    '<span class="cmd">python gmail/fetch_emails.py --dump</span>               <span class="arr">\u2192</span> <span class="desc">Save newest email HTML to gmail/ folder (for writing parsers)</span>\\n' +
    '<span class="cmd">python gmail/fetch_emails.py --dump-from notaires</span> <span class="arr">\u2192</span> <span class="desc">Same but from a specific source</span>\\n' +
    '<span class="cmd">python scorer/score.py --test</span>                     <span class="arr">\u2192</span> <span class="desc">Score a fake listing and print the result in terminal</span>\\n' +
    '<span class="cmd">python dashboard.py</span>                               <span class="arr">\u2192</span> <span class="desc">Start the dashboard at localhost:8501</span>' +
    '</pre></div>';
  function closeModal() { if (modal.parentNode) modal.parentNode.removeChild(modal); }
  modal.addEventListener('click', function(e) { if (e.target === modal) closeModal(); });
  modal.querySelector('.close-btn').addEventListener('click', closeModal);
  document.addEventListener('keydown', function handler(e) { if (e.key === 'Escape') { closeModal(); document.removeEventListener('keydown', handler); } });
  document.body.appendChild(modal);
});

function badgeClass(score) {
  if (score >= 9) return 'high';
  if (score >= 7) return 'mid';
  if (score >= 5) return 'low';
  return 'zero';
}

function renderCard(item) {
  const flags = (item.flags || []).map(f => {
    const text = typeof f === 'object' ? (f.flag + (f.note ? ': ' + f.note : '')) : f;
    return '<span class="flag">' + esc(text) + '</span>';
  }).join('');

  const features = (item.notable_features || []).map(f =>
    '<span class="feature">' + esc(f) + '</span>'
  ).join('');

  const starred = item.starred ? 'starred' : '';
  const starActive = item.starred ? 'active' : '';
  const starSymbol = item.starred ? '\u2605' : '\u2606';
  const notes = item.notes || '';

  var photos = item.photo_urls || [];
  var thumbHtml;
  if (photos.length > 0) {
    thumbHtml = '<div class="card-thumb"><img src="' + esc(photos[0]) + '" alt="" loading="lazy"></div>';
  } else {
    thumbHtml = '<div class="card-thumb"><div class="placeholder"><svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><path d="M32 6L4 30h8v24h16V38h8v16h16V30h8L32 6z" fill="#999"/></svg></div></div>';
  }

  var sourceClass = (item.source === 'seloger' || item.source === 'notaires' || item.source === 'leboncoin') ? item.source : 'other';

  return '<div class="card ' + starred + '" data-id="' + item.id + '">' +
    thumbHtml +
    '<div class="card-body">' +
    '<div class="card-top">' +
      '<div class="card-main">' +
        '<span class="badge ' + badgeClass(item.score) + '">' + item.score + '</span>' +
        '<div class="card-price">' + (item.price_eur != null ? item.price_eur.toLocaleString() + ' \u20ac' : 'Prix N/A') +
          (item.source ? ' <span class="source-tag ' + esc(sourceClass) + '">' + esc(item.source) + '</span>' : '') +
        '</div>' +
        (item.url ? '<h3><a href="' + esc(item.url) + '" target="_blank" class="title-link">' + esc(item.title || 'Untitled') + '</a></h3>' :
                    '<h3>' + esc(item.title || 'Untitled') + '</h3>') +
        '<div class="meta">' +
          '<span>' + esc(item.location || '') + '</span>' +
          (item.city_distance_km != null ? '<span class="geo-context">' + Math.round(item.city_distance_km) + ' km from ' + esc(item.nearest_city) + ' \u00b7 ' + Math.round(item.coast_distance_km) + ' km from coast</span>' : '') +
          (item.surface_m2 ? '<span>' + item.surface_m2 + ' m\u00b2</span>' : '') +
          (item.bedrooms ? '<span>' + item.bedrooms + ' bed</span>' : '') +
          (item.rooms ? '<span>' + item.rooms + ' rooms</span>' : '') +
          (item.property_type ? '<span>' + esc(item.property_type) + '</span>' : '') +
        '</div>' +
        (item.condition_estimate ? '<div class="condition">' + esc(item.condition_estimate) + '</div>' : '') +
        (item.reasoning ? '<div class="reasoning">' + esc(item.reasoning) + '</div>' : '') +
        (flags ? '<div class="flags">' + flags + '</div>' : '') +
        (features ? '<div class="features">' + features + '</div>' : '') +
      '</div>' +
    '</div>' +
    '<div class="card-actions">' +
      '<button class="star-btn ' + starActive + '" onclick="toggleStar(' + item.id + ', this)">' + starSymbol + '</button>' +
      '<input class="notes-input" placeholder="Add a note..." value="' + esc(notes) + '" id="notes-' + item.id + '">' +
      '<button class="save-btn" onclick="saveNotes(' + item.id + ')">Save</button>' +
    '</div>' +
    '</div>' +
  '</div>';
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

async function loadListings() {
  const params = new URLSearchParams({
    min_score: document.getElementById('f-score').value,
    days: document.getElementById('f-days').value,
    min_price: document.getElementById('f-min-price').value || '0',
    max_price: document.getElementById('f-max-price').value || '999999',
    region: stripAccents(document.getElementById('f-region').value),
    starred_only: document.getElementById('f-starred').checked,
    has_notes: document.getElementById('f-notes').checked,
  });
  const resp = await fetch('/api/listings?' + params);
  const data = await resp.json();
  document.getElementById('stats').textContent = data.length + ' listing(s)';
  document.getElementById('cards').innerHTML = data.map(renderCard).join('');
}

async function toggleStar(id, btn) {
  const resp = await fetch('/api/listings/' + id + '/star', { method: 'POST' });
  const data = await resp.json();
  loadListings();
}

async function saveNotes(id) {
  const notes = document.getElementById('notes-' + id).value;
  await fetch('/api/listings/' + id + '/notes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ notes }),
  });
}

// Reload on filter change
document.querySelectorAll('.filters select, .filters input').forEach(el => {
  el.addEventListener('change', loadListings);
});
document.getElementById('f-region').addEventListener('input', debounce(function() {
  var val = document.getElementById('f-region').value;
  activeDept = null;
  DEPARTMENTS.forEach(function(d) { if (d.code === val) activeDept = d.code; });
  renderDeptChips();
  loadListings();
}, 400));

function debounce(fn, ms) {
  let t; return function() { clearTimeout(t); t = setTimeout(fn, ms); };
}

renderDeptChips();
loadListings();
</script>
</body>
</html>"""


if __name__ == "__main__":
    print(f"Dashboard running at http://localhost:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
