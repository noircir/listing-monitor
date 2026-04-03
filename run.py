import argparse
import json
import time
import sys

from gmail.fetch_emails import authenticate, get_listing_emails, mark_as_processed
from parsers.seloger import parse_email as parse_seloger
from parsers.notaires import parse_email as parse_notaires
from storage.database import init_db, add_listing, get_all_listings, get_unscored_listings, add_score, delete_all_scores, deduplicate_listings, get_digest_listings
from scorer.score import score_listing


def main():
    parser = argparse.ArgumentParser(description="france-triage: fetch, parse, score, digest")
    parser.add_argument("--dry-run", action="store_true", help="Skip API scoring calls")
    parser.add_argument("--days", type=int, default=1, help="Fetch emails from last N days (default: 1)")
    parser.add_argument("--rescore", action="store_true", help="Delete all scores and rescore every listing")
    parser.add_argument("--dedup", action="store_true", help="Deduplicate listings and exit")
    args = parser.parse_args()

    since_hours = args.days * 24

    # Ensure database exists
    init_db()

    # --- Dedup-only mode ---
    if args.dedup:
        deduplicate_listings()
        return

    # --- Rescore mode: skip fetch, just rescore everything ---
    if args.rescore:
        all_listings = get_all_listings()
        if not all_listings:
            print("No listings in database to rescore.")
            return

        print(f"Deleting all existing scores...")
        delete_all_scores()
        print(f"Rescoring {len(all_listings)} listing(s)...")

        total_scored = 0
        total_high = 0
        for listing in all_listings:
            result = score_listing(listing)
            add_score(listing["id"], result)
            total_scored += 1

            score_val = result.get("score", 0)
            if score_val >= 7:
                total_high += 1

            print(f"  [{total_scored}/{len(all_listings)}] {listing['title']} → score {score_val}")

            if total_scored < len(all_listings):
                time.sleep(1)

        print(f"\n--- Rescore Summary ---")
        print(f"  Total rescored:  {total_scored}")
        print(f"  Scored 7+:       {total_high}")
        return

    # --- Step 1: Fetch emails ---
    print(f"Fetching listing emails from the last {args.days} day(s)...")
    service = authenticate()
    emails = get_listing_emails(service=service, since_hours=since_hours)
    print(f"  {len(emails)} email(s) fetched")

    # --- Step 2-3: Parse and store listings ---
    total_parsed = 0
    total_new = 0
    email_ids_to_mark = []

    for email in emails:
        sender = email.get("sender", "")
        if "notaires" in sender.lower():
            listings = parse_notaires(email["html_body"])
        else:
            listings = parse_seloger(email["html_body"])
        total_parsed += len(listings)

        for listing in listings:
            listing_id = add_listing(listing)
            if listing_id is not None:
                total_new += 1

        email_ids_to_mark.append(email["message_id"])

    print(f"  {total_parsed} listing(s) parsed")
    print(f"  {total_new} new listing(s) added to database")

    # --- Step 4-6: Score unscored listings ---
    unscored = get_unscored_listings()
    total_scored = 0
    total_high = 0

    if args.dry_run:
        print(f"  {len(unscored)} unscored listing(s) — skipping scoring (dry run)")
    else:
        print(f"  Scoring {len(unscored)} listing(s)...")
        for listing in unscored:
            result = score_listing(listing)
            add_score(listing["id"], result)
            total_scored += 1

            score_val = result.get("score", 0)
            if score_val >= 7:
                total_high += 1

            print(f"    [{total_scored}/{len(unscored)}] {listing['title']} → score {score_val}")

            # Rate-limit API calls
            if total_scored < len(unscored):
                time.sleep(1)

    # --- Dedup ---
    deduplicate_listings()

    # --- Step 7: Mark emails as processed ---
    for mid in email_ids_to_mark:
        mark_as_processed(mid)

    # --- Step 8: Summary ---
    print("\n--- Summary ---")
    print(f"  Emails fetched:    {len(emails)}")
    print(f"  Listings parsed:   {total_parsed}")
    print(f"  New listings:      {total_new}")
    if not args.dry_run:
        print(f"  Listings scored:   {total_scored}")
        print(f"  Scored 7+:         {total_high}")

        # --- Step 9: Show high-scoring listings ---
        digest = get_digest_listings(min_score=7)
        if digest:
            print(f"\n--- Top Listings (score >= 7) ---")
            for row in digest:
                print(f"\n  {row['title']}")
                print(f"    Price:    {row['price_eur']}€")
                print(f"    Location: {row['location']}")
                print(f"    Surface:  {row['surface_m2']} m²")
                print(f"    Score:    {row['score']}/10")
                print(f"    Reasoning: {row['reasoning']}")
                flags = json.loads(row['flags']) if row['flags'] else []
                if flags:
                    print(f"    Flags:")
                    for flag in flags:
                        if isinstance(flag, dict):
                            print(f"      - {flag.get('flag', '')}: {flag.get('note', '')}")
                        else:
                            print(f"      - {flag}")
        else:
            print("\n  No listings scored 7+ in the last 24 hours.")
    else:
        print(f"  Unscored listings: {len(unscored)}")
        print("\n  (Dry run — no scoring performed)")


if __name__ == "__main__":
    main()
