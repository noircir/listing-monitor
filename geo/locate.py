import json as _json
import math
import os
import sqlite3
import time
import httpx

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "storage", "properties.db")
GEO_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "geo-references.json")

_last_request_time = 0.0


def _load_geo_references():
    """Load reference cities and coast points from config. Returns (cities, coast) or (None, None)."""
    if not os.path.exists(GEO_CONFIG_PATH):
        return None, None
    try:
        with open(GEO_CONFIG_PATH, "r") as f:
            data = _json.load(f)
        cities = {name: tuple(coords) for name, coords in data.get("reference_cities", {}).items()}
        coast = [tuple(p) for p in data.get("coast_points", [])]
        return cities or None, coast or None
    except Exception:
        return None, None


def _haversine(lat1, lon1, lat2, lon2):
    """Distance in km between two lat/lng points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _ensure_geo_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geo_cache (
            location_string TEXT PRIMARY KEY,
            lat REAL,
            lng REAL,
            nearest_city TEXT,
            city_distance_km REAL,
            coast_distance_km REAL
        )
    """)
    conn.commit()
    conn.close()


def _get_cached(location_string):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM geo_cache WHERE location_string = ?", (location_string,)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def _save_cache(location_string, lat, lng, nearest_city, city_distance_km, coast_distance_km):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT OR REPLACE INTO geo_cache
           (location_string, lat, lng, nearest_city, city_distance_km, coast_distance_km)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (location_string, lat, lng, nearest_city, city_distance_km, coast_distance_km),
    )
    conn.commit()
    conn.close()


def _geocode(location_string, country=None):
    """Geocode a location string using Nominatim. Returns (lat, lng) or None."""
    global _last_request_time

    # Respect 1 req/sec rate limit
    elapsed = time.time() - _last_request_time
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)

    query = location_string
    if country:
        query += f", {country}"

    try:
        resp = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": "listing-monitor/1.0"},
            timeout=10,
        )
        _last_request_time = time.time()

        if resp.status_code == 200 and resp.json():
            result = resp.json()[0]
            return float(result["lat"]), float(result["lon"])
    except Exception:
        pass

    return None


def _find_nearest_city(lat, lng, reference_cities):
    """Returns (city_name, distance_km) for the nearest reference city."""
    best_name = None
    best_dist = float("inf")
    for name, (clat, clng) in reference_cities.items():
        d = _haversine(lat, lng, clat, clng)
        if d < best_dist:
            best_dist = d
            best_name = name
    return best_name, round(best_dist, 1)


def _find_coast_distance(lat, lng, coast_points):
    """Returns distance in km to the nearest coast point."""
    return round(min(_haversine(lat, lng, clat, clng) for clat, clng in coast_points), 1)


def get_geo_info(location_string):
    """Get geographic context for a location string.

    Returns dict {lat, lng, nearest_city, city_distance_km, coast_distance_km}
    or None if geocoding fails or geo config is missing.
    """
    reference_cities, coast_points = _load_geo_references()
    if not reference_cities:
        return None

    _ensure_geo_table()

    cached = _get_cached(location_string)
    if cached:
        return {
            "lat": cached["lat"],
            "lng": cached["lng"],
            "nearest_city": cached["nearest_city"],
            "city_distance_km": cached["city_distance_km"],
            "coast_distance_km": cached["coast_distance_km"],
        }

    coords = _geocode(location_string)
    if not coords:
        return None

    lat, lng = coords
    nearest_city, city_dist = _find_nearest_city(lat, lng, reference_cities)

    coast_dist = None
    if coast_points:
        coast_dist = _find_coast_distance(lat, lng, coast_points)

    _save_cache(location_string, lat, lng, nearest_city, city_dist, coast_dist)

    return {
        "lat": lat,
        "lng": lng,
        "nearest_city": nearest_city,
        "city_distance_km": city_dist,
        "coast_distance_km": coast_dist,
    }


if __name__ == "__main__":
    reference_cities, coast_points = _load_geo_references()
    if not reference_cities:
        print(f"No geo config found at {GEO_CONFIG_PATH}")
        print("Create it with reference_cities and coast_points. See config/sample-geo-references.json.")
        raise SystemExit(1)

    print(f"Loaded {len(reference_cities)} reference cities, {len(coast_points or [])} coast points\n")

    test_locations = list(reference_cities.keys())[:3]
    if not test_locations:
        print("No reference cities to test with.")
        raise SystemExit(1)

    for city_name in test_locations:
        print(f"Geocoding: {city_name}")
        info = get_geo_info(city_name)
        if info:
            print(f"  {_json.dumps(info)}")
        else:
            print("  (failed)")
        print()
