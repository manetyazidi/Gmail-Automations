"""One-time helper to generate token.json for Gmail OAuth in a codespace.

Codespaces can't run a local browser, so the standard run_local_server
flow fails. This script does it manually: prints the auth URL, you
open it, approve, paste the redirected URL back here, and it writes
token.json.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google_auth_oauthlib.flow import Flow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
REDIRECT_URI = "http://localhost:8080/"


def main() -> int:
    creds_path = Path("credentials.json")
    if not creds_path.exists():
        print("ERROR: credentials.json not found in current directory.")
        print("Download your OAuth client JSON from Google Cloud Console")
        print("and save it here as 'credentials.json' first.")
        return 1

    flow = Flow.from_client_secrets_file(
        str(creds_path), scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )

    print()
    print("=" * 70)
    print("STEP 1: Open this URL in your browser, sign in, and approve:")
    print()
    print(auth_url)
    print()
    print("STEP 2: Your browser will redirect to a 'localhost' page that")
    print("        FAILS to load. That is expected.")
    print("        Copy the FULL URL from your browser's address bar")
    print("        (it looks like http://localhost:8080/?code=4/0AX...&...)")
    print("        and paste it below.")
    print("=" * 70)
    print()

    pasted = input("Paste the full redirected URL here: ").strip()
    if not pasted:
        print("ERROR: empty input")
        return 1

    parsed = urlparse(pasted)
    code = parse_qs(parsed.query).get("code", [None])[0]
    if not code:
        print("ERROR: could not find 'code' in that URL.")
        return 1

    flow.fetch_token(code=code)
    creds = flow.credentials
    Path("token.json").write_text(creds.to_json())
    print()
    print("OK: token.json written. You can now run: python account_news.py --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
