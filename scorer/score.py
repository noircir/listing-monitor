import json
import os
import re
import anthropic

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "property-profile.json")


def _load_profile():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def _pre_filter(listing, profile):
    """Apply hard constraint pre-filters. Returns (pass, failures) tuple."""
    failures = []
    hard = profile["hard_constraints"]
    rejected_types = profile["property_types"]["rejected"]

    price = listing.get("price_eur")
    surface = listing.get("surface_m2")
    prop_type = (listing.get("property_type") or "").lower()

    if price is not None and price > hard["price_ceiling_eur"]:
        failures.append(f"Price {price}€ exceeds ceiling of {hard['price_ceiling_eur']}€")

    if price is not None and surface is not None and surface < hard["min_surface_m2"]:
        failures.append(f"Surface {surface} m² below minimum {hard['min_surface_m2']} m²")

    for rejected in rejected_types:
        rejected_lower = rejected.lower()
        if prop_type and (prop_type in rejected_lower or rejected_lower in prop_type):
            failures.append(f"Rejected property type: {rejected}")
            break

    return len(failures) == 0, failures


def _build_prompt(listing, profile):
    """Build the scoring prompt for Claude."""
    # Prepare listing data without raw_html to save tokens
    listing_data = {k: v for k, v in listing.items() if k != "raw_html"}

    return f"""You are a listing scorer. Use the property profile below to evaluate the listing.

## Property Profile (scoring rules)

{json.dumps(profile, indent=2, ensure_ascii=False)}

## Listing to Score

{json.dumps(listing_data, indent=2, ensure_ascii=False)}

## Instructions

Score this listing according to the property profile above. Apply the rules exactly:

1. **Hard constraints**: Check all hard_constraints in the profile. Any failure = score 0 with hard_constraint_pass=false.

2. **Price scoring**: Apply the price_scoring tiers from the profile.

3. **Surface scoring**: Apply the surface_scoring tiers from the profile. If surface is not listed, flag it but don't reject.

4. **Region scoring**: Apply the target_regions rules from the profile.

5. **Condition**: Apply the condition scoring rules and preferences from the profile.

6. **Flags**: Check the flags_not_rejects list — surface these with notes, don't auto-reject.

7. **Features**: Note any features from the observe_and_report list.

8. **Note**: When bedrooms are estimated from rooms-1, this is approximate. Don't penalize if the listing doesn't specify bedrooms explicitly.

9. **IMPORTANT**: Photo count from email alerts is NOT reliable. Alert emails typically include only 1 thumbnail per listing regardless of how many photos exist on the actual listing page. Do NOT flag listings for having limited photos or only 1 photo. This is an email parsing limitation, not a listing quality signal.

Return ONLY valid JSON matching this exact format (no markdown, no explanation outside the JSON):

{{
  "score": <integer 0-10>,
  "hard_constraint_pass": <boolean>,
  "hard_constraint_failures": [<list of strings>],
  "flags": [<list of {{"flag": "...", "note": "..."}}>],
  "price_eur": <integer>,
  "bedrooms": <integer or "unknown">,
  "surface_m2": <number or "unknown">,
  "location": "<commune, department>",
  "property_type": "<string>",
  "condition_estimate": "<string>",
  "notable_features": [<list of strings from observe_and_report>],
  "reasoning": "<2-3 sentences>",
  "listing_url": "<string or null>"
}}"""


def score_listing(listing):
    """Score a single listing. Returns a score dict."""
    profile = _load_profile()

    # Pre-filter before API call
    passed, failures = _pre_filter(listing, profile)
    if not passed:
        return {
            "score": 0,
            "hard_constraint_pass": False,
            "hard_constraint_failures": failures,
            "flags": [],
            "notable_features": [],
            "reasoning": f"Pre-filter rejection: {'; '.join(failures)}",
            "condition_estimate": "unknown",
        }

    # Call Claude API
    prompt = _build_prompt(listing, profile)

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text
    except Exception as e:
        return {
            "score": 0,
            "hard_constraint_pass": False,
            "hard_constraint_failures": [],
            "flags": [],
            "notable_features": [],
            "reasoning": f"API call error: {e}",
            "condition_estimate": "unknown",
        }

    # Parse JSON response
    try:
        # Strip markdown fences if the model wraps it
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return {
            "score": 0,
            "hard_constraint_pass": False,
            "hard_constraint_failures": [],
            "flags": [],
            "notable_features": [],
            "reasoning": f"API parse error: {e}. Raw response: {raw_text[:200]}",
            "condition_estimate": "unknown",
        }

    return result


if __name__ == "__main__":
    import sys

    if "--test" not in sys.argv:
        print("Usage: python scorer/score.py --test")
        print("Scores a hardcoded sample listing for testing.")
        sys.exit(1)

    sample = {
        "source": "example_site",
        "title": "House 70 m² Example Town",
        "price_eur": 35000,
        "location": "Example Town (12345)",
        "bedrooms": 2,
        "rooms": 3,
        "surface_m2": 70.0,
        "land_m2": None,
        "property_type": "House",
        "description": "House 70 m2, 3 rooms, Example Town (12345)",
        "photo_urls": "[]",
        "energy_class": None,
    }

    print("Scoring sample listing...")
    print(f"  {sample['title']} — {sample['price_eur']}€\n")

    result = score_listing(sample)
    print(json.dumps(result, indent=2, ensure_ascii=False))
