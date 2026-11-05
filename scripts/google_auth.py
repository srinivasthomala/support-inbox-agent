"""One-time Google login for the support mailbox.

    python scripts/google_auth.py path/to/client_secret.json

Sign in as the *support* Gmail account (not your personal one) and approve
Gmail + Sheets access. The client id, client secret and refresh token are
written into .env (never printed), ready to copy into Fly secrets.
"""
import json
import re
import sys
from pathlib import Path

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
if not creds.refresh_token:
    sys.exit("Google returned no refresh token. Remove the app's access at myaccount.google.com/permissions and retry.")

values = {
    "GOOGLE_CLIENT_ID": client["client_id"],
    "GOOGLE_CLIENT_SECRET": client["client_secret"],
    "GOOGLE_REFRESH_TOKEN": creds.refresh_token,
}
env = Path(".env")
text = env.read_text(encoding="utf-8") if env.exists() else ""
for key, value in values.items():
    line = f"{key}={value}"
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        text = re.sub(rf"^{key}=.*$", lambda _: line, text, flags=re.M)
    else:
        text += f"\n{line}"
env.write_text(text, encoding="utf-8")
print(f"Saved GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN to {env.resolve()}")
