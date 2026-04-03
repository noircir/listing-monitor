"""Example parser for a European real estate listing alert email.

Demonstrates the pattern: find listing blocks in HTML, extract
price/type/surface/location using anchor name attributes.
Adapt the selectors for your own listing site.
"""

import re
import json
from bs4 import BeautifulSoup, Comment


def parse_email(html_body):
    """Parse a listing alert email and extract all listings.

    Returns a list of dicts matching the listings table schema.
    """
    soup = BeautifulSoup(html_body, "lxml")

    # Find listing blocks between <!--LISTING--> and <!--END LISTING--> comments
    listing_blocks = _extract_listing_blocks(soup, html_body)

    if not listing_blocks:
        # Fallback: try to find listings by the named anchor pattern
        listing_blocks = _extract_listings_by_anchors(soup)

    results = []
    for block in listing_blocks:
        listing = _parse_listing_block(block)
        if listing:
            results.append(listing)

    return results


def _extract_listing_blocks(soup, html_body):
    """Split HTML into listing blocks using <!--LISTING--> / <!--END LISTING--> comments.

    Many listing alert emails wrap each property in HTML comment markers.
    Adjust the marker text to match your site's email format.
    """
    blocks = []
    pattern = re.compile(
        r'<!--\s*LISTING\s*-->(.*?)<!--\s*END LISTING\s*-->',
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(html_body):
        block_html = match.group(1)
        block_soup = BeautifulSoup(block_html, "lxml")
        blocks.append(block_soup)

    return blocks


def _extract_listings_by_anchors(soup):
    """Fallback: group named anchors into listing blocks when comments are absent.

    Uses the price anchor as a starting point and walks up the DOM to find
    the containing table. Adjust the anchor name and parent selector for
    your site's HTML structure.
    """
    price_links = soup.find_all("a", attrs={"name": "adprice"})
    if not price_links:
        return []

    blocks = []
    for link in price_links:
        container = link.find_parent("table", class_="container-90")
        if container:
            blocks.append(container)
        else:
            container = link.find_parent("table")
            if container:
                blocks.append(container)
    return blocks


def _parse_listing_block(block):
    """Extract structured data from a single listing block.

    Each _parse_* function targets a specific named anchor (<a name="...">)
    in the email HTML. Replace these with the selectors from your listing site.
    """
    price_eur = _parse_price(block)
    property_type = _parse_property_type(block)
    surface_m2 = _parse_surface(block)
    rooms = _parse_rooms(block)
    bedrooms = _parse_bedrooms(block, rooms)
    location = _parse_location(block)
    listing_url = _parse_listing_url(block)
    photo_url = _parse_photo_url(block)

    # If we couldn't extract any meaningful data, this is probably not a real listing
    if price_eur is None and property_type is None and location is None:
        return None

    # Build title from available parts
    parts = []
    if property_type:
        parts.append(property_type)
    if surface_m2:
        parts.append(f"{surface_m2} m²")
    if location:
        parts.append(location)
    title = " — ".join(parts) if parts else "Listing"

    # Build description from type + criteria + location
    desc_parts = []
    if property_type:
        desc_parts.append(property_type)
    if surface_m2:
        desc_parts.append(f"{surface_m2} m²")
    if rooms:
        desc_parts.append(f"{rooms} pièces")
    if bedrooms:
        desc_parts.append(f"{bedrooms} chambres")
    if location:
        desc_parts.append(location)
    description = ", ".join(desc_parts)

    photo_urls = [photo_url] if photo_url else []

    return {
        "source": "example_listing_site",
        "url": listing_url,
        "title": title,
        "price_eur": price_eur,
        "location": location,
        "bedrooms": bedrooms,
        "rooms": rooms,
        "surface_m2": surface_m2,
        "land_m2": None,
        "property_type": property_type,
        "description": description,
        "photo_urls": photo_urls,
        "energy_class": None,
        "date_posted": None,
        "raw_html": str(block),
    }


def _parse_price(block):
    """Extract price from name='adprice' link. Returns integer or None.

    Handles European price formatting like "35 000 €" (spaces as thousands separator).
    Adjust the currency symbol and "price not listed" text for your site.
    """
    link = block.find("a", attrs={"name": "adprice"})
    if not link:
        return None

    text = link.get_text()
    # Skip listings where price is not disclosed
    if "prix non communiqué" in text.lower():
        return None

    # Get the price from the bold/strong part (before the €/m² part)
    strong = link.find("strong")
    price_text = strong.get_text() if strong else text

    # Extract digits from something like "35 000 €"
    digits = re.sub(r"[^\d]", "", price_text.split("€")[0])
    if digits:
        return int(digits)
    return None


def _parse_property_type(block):
    """Extract property type from name='adtype'. E.g. 'Maison' from 'Maison 70 m2'."""
    link = block.find("a", attrs={"name": "adtype"})
    if not link:
        return None

    text = link.get_text(strip=True)
    # Remove the surface part (numbers + m2/m²)
    prop_type = re.sub(r"\d[\d\s,.]*m[²2]?", "", text).strip()
    return prop_type if prop_type else None


def _parse_surface(block):
    """Extract surface area in m² from adtype or adcriteria."""
    for name in ("adtype", "adcriteria"):
        link = block.find("a", attrs={"name": name})
        if not link:
            continue
        text = link.get_text(strip=True)
        match = re.search(r"([\d\s,.]+)\s*m[²2]", text)
        if match:
            num_str = match.group(1).replace(" ", "").replace(",", ".")
            try:
                return float(num_str)
            except ValueError:
                continue
    return None


def _parse_rooms(block):
    """Extract number of rooms (pièces) from adcriteria."""
    link = block.find("a", attrs={"name": "adcriteria"})
    if not link:
        return None
    text = link.get_text(strip=True)
    match = re.search(r"(\d+)\s*pièce", text)
    if match:
        return int(match.group(1))
    return None


def _parse_bedrooms(block, rooms):
    """Extract bedrooms (chambres) from adcriteria. Estimate from rooms if absent.

    French convention: "pièces" includes the living room, so bedrooms ≈ rooms - 1.
    Adjust this heuristic for your market.
    """
    link = block.find("a", attrs={"name": "adcriteria"})
    if link:
        text = link.get_text(strip=True)
        match = re.search(r"(\d+)\s*chambre", text)
        if match:
            return int(match.group(1))

    if rooms and rooms > 1:
        return rooms - 1
    return None


def _parse_location(block):
    """Extract commune and postal code from adlocation."""
    link = block.find("a", attrs={"name": "adlocation"})
    if not link:
        return None
    text = link.get_text(strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text if text else None


def _parse_listing_url(block):
    """Extract listing URL from adbutton (the 'View listing' CTA)."""
    link = block.find("a", attrs={"name": "adbutton"})
    if not link:
        return None
    return link.get("href")


def _parse_photo_url(block):
    """Extract photo URL from Background attribute or adimage link."""
    # Try Background attribute on td (common in email HTML for hero images)
    td = block.find("td", attrs={"background": True})
    if td:
        return td.get("background") or td.get("Background")

    # Fallback to adimage link's img
    link = block.find("a", attrs={"name": "adimage"})
    if link:
        img = link.find("img")
        if img and img.get("src"):
            return img["src"]

    return None


if __name__ == "__main__":
    import os

    sample_path = os.path.join(
        os.path.dirname(__file__), "..", "gmail", "sample_email.html"
    )

    if not os.path.exists(sample_path):
        print(f"Sample email not found at {sample_path}")
        print("Save a listing alert email as gmail/sample_email.html first.")
        print("(Use: python gmail/fetch_emails.py --dump)")
        raise SystemExit(1)

    with open(sample_path, "r") as f:
        html = f.read()

    listings = parse_email(html)

    print(f"Found {len(listings)} listing(s):\n")
    for i, listing in enumerate(listings, 1):
        display = {k: v for k, v in listing.items() if k != "raw_html"}
        print(f"--- Listing {i} ---")
        print(json.dumps(display, indent=2, ensure_ascii=False))
        print()
