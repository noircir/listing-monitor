import sqlite3
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "properties.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY,
            source TEXT,
            url TEXT UNIQUE,
            title TEXT,
            price_eur INTEGER,
            location TEXT,
            bedrooms INTEGER,
            rooms INTEGER,
            surface_m2 REAL,
            land_m2 REAL,
            property_type TEXT,
            description TEXT,
            photo_urls TEXT,
            energy_class TEXT,
            date_posted TEXT,
            date_found TEXT DEFAULT CURRENT_TIMESTAMP,
            raw_html TEXT
        );

        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY,
            listing_id INTEGER NOT NULL REFERENCES listings(id),
            score INTEGER,
            hard_constraint_pass BOOLEAN,
            flags TEXT,
            notable_features TEXT,
            reasoning TEXT,
            condition_estimate TEXT,
            date_scored TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


def add_listing(data_dict):
    """Insert a listing. If URL already exists, skip and return None."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO listings
               (source, url, title, price_eur, location, bedrooms, rooms,
                surface_m2, land_m2, property_type, description, photo_urls,
                energy_class, date_posted, raw_html)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data_dict.get("source"),
                data_dict.get("url"),
                data_dict.get("title"),
                data_dict.get("price_eur"),
                data_dict.get("location"),
                data_dict.get("bedrooms"),
                data_dict.get("rooms"),
                data_dict.get("surface_m2"),
                data_dict.get("land_m2"),
                data_dict.get("property_type"),
                data_dict.get("description"),
                json.dumps(data_dict.get("photo_urls", [])),
                data_dict.get("energy_class"),
                data_dict.get("date_posted"),
                data_dict.get("raw_html"),
            ),
        )
        conn.commit()
        listing_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return listing_id
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def get_all_listings():
    """Return all listings."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM listings").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_all_scores():
    """Delete all scores from the database."""
    conn = get_connection()
    conn.execute("DELETE FROM scores")
    conn.commit()
    conn.close()


def deduplicate_listings():
    """Remove duplicate listings that share (price, location, surface, type).
    Keeps the one with the longest description and photos. Returns count removed."""
    conn = get_connection()
    groups = conn.execute(
        """SELECT price_eur, location, surface_m2, property_type, COUNT(*) as cnt
           FROM listings
           WHERE price_eur IS NOT NULL
           GROUP BY price_eur, location, surface_m2, property_type
           HAVING cnt > 1"""
    ).fetchall()

    removed = 0
    for g in groups:
        rows = conn.execute(
            """SELECT id, description, photo_urls FROM listings
               WHERE price_eur = ? AND location = ? AND surface_m2 = ? AND property_type = ?
               ORDER BY LENGTH(COALESCE(description, '')) DESC,
                        LENGTH(COALESCE(photo_urls, '[]')) DESC,
                        id ASC""",
            (g["price_eur"], g["location"], g["surface_m2"], g["property_type"]),
        ).fetchall()

        keep_id = rows[0]["id"]
        for row in rows[1:]:
            conn.execute("DELETE FROM scores WHERE listing_id = ?", (row["id"],))
            conn.execute("DELETE FROM listings WHERE id = ?", (row["id"],))
            removed += 1

    conn.commit()
    conn.close()
    print(f"  Dedup: removed {removed} duplicate listing(s)")
    return removed


def get_unscored_listings():
    """Return listings that don't have a score yet."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT l.* FROM listings l
           LEFT JOIN scores s ON l.id = s.listing_id
           WHERE s.id IS NULL"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_score(listing_id, score_dict):
    """Save a score for a listing."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO scores
           (listing_id, score, hard_constraint_pass, flags,
            notable_features, reasoning, condition_estimate)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            listing_id,
            score_dict.get("score"),
            score_dict.get("hard_constraint_pass"),
            json.dumps(score_dict.get("flags", [])),
            json.dumps(score_dict.get("notable_features", [])),
            score_dict.get("reasoning"),
            score_dict.get("condition_estimate"),
        ),
    )
    conn.commit()
    conn.close()


def get_digest_listings(min_score=7):
    """Return listings scored min_score+ from the last 24 hours, joined with scores."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT l.*, s.score, s.hard_constraint_pass, s.flags,
                  s.notable_features, s.reasoning, s.condition_estimate,
                  s.date_scored
           FROM listings l
           JOIN scores s ON s.id = (
               SELECT s2.id FROM scores s2
               WHERE s2.listing_id = l.id
               ORDER BY s2.id DESC LIMIT 1
           )
           WHERE s.score >= ?
             AND s.date_scored >= datetime('now', '-1 day')
           ORDER BY s.score DESC""",
        (min_score,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    # Quick self-test
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    init_db()
    print("DB initialized at", DB_PATH)

    # Insert a fake listing
    fake = {
        "source": "example_site",
        "url": "https://example.com/listing/123",
        "title": "3-room house - Example Town",
        "price_eur": 29000,
        "location": "Example Town (12345)",
        "bedrooms": 2,
        "rooms": 3,
        "surface_m2": 55.0,
        "land_m2": None,
        "property_type": "village house",
        "description": "Charming village house with rooftop terrace.",
        "photo_urls": ["https://example.com/img/1.jpg", "https://example.com/img/2.jpg"],
        "energy_class": "F",
        "date_posted": "2026-04-01",
        "raw_html": "<div>fake</div>",
    }

    lid = add_listing(fake)
    print(f"Inserted listing id={lid}")

    # Duplicate should return None
    dup = add_listing(fake)
    print(f"Duplicate insert returned: {dup}")

    # Should appear as unscored
    unscored = get_unscored_listings()
    print(f"Unscored listings: {len(unscored)}")
    print(f"  -> {unscored[0]['title']} | {unscored[0]['price_eur']} EUR")

    # Score it
    add_score(lid, {
        "score": 8,
        "hard_constraint_pass": True,
        "flags": [{"flag": "DPE F", "note": "expected at this price"}],
        "notable_features": ["terrace", "stone construction"],
        "reasoning": "Good price for 55m² village house. Energy class F is normal for this budget range.",
        "condition_estimate": "habitable but dated",
    })
    print("Score added.")

    # Should no longer be unscored
    unscored_after = get_unscored_listings()
    print(f"Unscored after scoring: {len(unscored_after)}")

    # Should appear in digest
    digest = get_digest_listings(min_score=7)
    print(f"Digest listings (score >= 7): {len(digest)}")
    print(f"  -> {digest[0]['title']} | score={digest[0]['score']}")
