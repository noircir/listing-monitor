import os
import sys
import base64
import datetime
import time
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

DIR = os.path.dirname(__file__)
CREDENTIALS_PATH = os.path.join(DIR, "credentials.json")
TOKEN_PATH = os.path.join(DIR, "token.json")
PROCESSED_IDS_PATH = os.path.join(DIR, "processed_ids.txt")


def authenticate():
    """Authenticate with Gmail via OAuth. Opens browser on first run."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(
                    f"Missing {CREDENTIALS_PATH}. Download it from Google Cloud Console "
                    f"(APIs & Services > Credentials > OAuth 2.0 Client ID > Download JSON)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _load_processed_ids():
    """Load already-processed message IDs from disk."""
    if not os.path.exists(PROCESSED_IDS_PATH):
        return set()
    with open(PROCESSED_IDS_PATH, "r") as f:
        return {line.strip() for line in f if line.strip()}


def mark_as_processed(message_id):
    """Append a message_id to the processed IDs file."""
    with open(PROCESSED_IDS_PATH, "a") as f:
        f.write(message_id + "\n")


def get_listing_emails(service=None, since_hours=24):
    """Fetch listing alert emails from the last since_hours.

    Searches for emails from SeLoger and immobilier.notaires.fr.
    Returns a list of dicts: {message_id, subject, date, sender, html_body}
    Skips emails already in processed_ids.txt.
    """
    if service is None:
        service = authenticate()

    processed = _load_processed_ids()

    after_date = datetime.datetime.now() - datetime.timedelta(hours=since_hours)
    after_str = after_date.strftime("%Y/%m/%d")
    query = f"(from:seloger.com OR from:immobilier.notaires.fr) after:{after_str}"

    # Paginate through all matching messages
    message_ids = []
    page_token = None
    while True:
        response = service.users().messages().list(
            userId="me", q=query, pageToken=page_token
        ).execute()

        if "messages" in response:
            message_ids.extend(m["id"] for m in response["messages"])

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    # Fetch each message, skipping already-processed ones
    results = []
    for mid in message_ids:
        if mid in processed:
            continue

        msg = _fetch_message_with_retry(service, mid)
        if msg is None:
            continue

        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        subject = headers.get("Subject", "(no subject)")
        date = headers.get("Date", "")
        sender = headers.get("From", "")

        html_body = _extract_html_body(msg["payload"])

        results.append({
            "message_id": mid,
            "subject": subject,
            "date": date,
            "sender": sender,
            "html_body": html_body,
        })

    return results


# Backward-compatible alias
get_seloger_emails = get_listing_emails


def _fetch_emails_from(service, sender_query, since_hours=168):
    """Fetch emails matching a sender query. No processed-ID filtering."""
    after_date = datetime.datetime.now() - datetime.timedelta(hours=since_hours)
    after_str = after_date.strftime("%Y/%m/%d")
    query = f"from:{sender_query} after:{after_str}"

    message_ids = []
    page_token = None
    while True:
        response = service.users().messages().list(
            userId="me", q=query, pageToken=page_token
        ).execute()
        if "messages" in response:
            message_ids.extend(m["id"] for m in response["messages"])
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    results = []
    for mid in message_ids:
        msg = _fetch_message_with_retry(service, mid)
        if msg is None:
            continue
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        results.append({
            "message_id": mid,
            "subject": headers.get("Subject", "(no subject)"),
            "date": headers.get("Date", ""),
            "sender": headers.get("From", ""),
            "html_body": _extract_html_body(msg["payload"]),
        })
    return results


def _fetch_message_with_retry(service, message_id, max_retries=3):
    """Fetch a single Gmail message with retry on transient errors. Returns None if all retries fail."""
    for attempt in range(max_retries):
        try:
            return service.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"    Retry {attempt + 1}/{max_retries} for message {message_id} (waiting {wait}s): {e}")
                time.sleep(wait)
            else:
                print(f"    Skipping message {message_id} after {max_retries} retries: {e}")
                return None


def _extract_html_body(payload):
    """Recursively extract the HTML body from a Gmail message payload."""
    if payload.get("mimeType") == "text/html" and "body" in payload:
        data = payload["body"].get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        html = _extract_html_body(part)
        if html:
            return html

    return ""


if __name__ == "__main__":
    dump_mode = "--dump" in sys.argv

    # --dump-from <keyword>: save the most recent email from a specific sender
    dump_from = None
    if "--dump-from" in sys.argv:
        idx = sys.argv.index("--dump-from")
        if idx + 1 < len(sys.argv):
            dump_from = sys.argv[idx + 1]

    print("Authenticating with Gmail...")
    service = authenticate()
    print("Authenticated.\n")

    if dump_from:
        sender_map = {
            "seloger": "seloger.com",
            "notaires": "immobilier.notaires.fr",
        }
        sender_query = sender_map.get(dump_from, dump_from)
        print(f"Fetching emails from {sender_query} (last 30 days)...")
        emails = _fetch_emails_from(service, sender_query, since_hours=720)
        if not emails:
            print(f"No emails found from {sender_query}.")
        else:
            filename = f"sample_{dump_from}.html"
            sample_path = os.path.join(DIR, filename)
            with open(sample_path, "w") as f:
                f.write(emails[0]["html_body"])
            print(f"Saved most recent email to {sample_path}")
            print(f"  Subject: {emails[0]['subject']}")
    else:
        print("Fetching listing emails from the last 7 days...")
        emails = get_listing_emails(service=service, since_hours=168)

        if dump_mode:
            if not emails:
                print("No listing emails found to dump.")
            else:
                sample_path = os.path.join(DIR, "sample_email.html")
                with open(sample_path, "w") as f:
                    f.write(emails[0]["html_body"])
                print(f"Saved sample email to {sample_path}")
        else:
            print(f"\nFound {len(emails)} listing email(s):\n")
            for email in emails:
                print(f"  [{email['date']}] ({email['sender'][:30]})")
                print(f"  {email['subject']}")
                print(f"  (id: {email['message_id']})")
                print()
