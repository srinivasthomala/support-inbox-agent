"""One-time Google login for the support mailbox. Prints the refresh token.

    python scripts/google_auth.py path/to/client_secret.json

Sign in as the *support* Gmail account (not your personal one) and approve
Gmail + Sheets access. Put the printed values into .env / Fly secrets.
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/spreadsheets",
]

if len(sys.argv) != 2:
    sys.exit(__doc__)

flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
client = json.load(open(sys.argv[1]))["installed"]
print("\nAdd these to .env / fly secrets:\n")
print(f"GOOGLE_CLIENT_ID={client['client_id']}")
print(f"GOOGLE_CLIENT_SECRET={client['client_secret']}")
print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
