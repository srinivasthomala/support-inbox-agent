"""Reset the demo before a recording.

    python scripts/reset_demo.py

- Orders go back to their starting state (no refunds, fresh dates), so Leo's
  order is refundable again and Sam's is still outside the window.
- Rows in the Google Sheet's first tab are copied to a "Test runs" tab, then
  cleared, leaving only the header.

Processed-email records are kept, so nothing already answered is answered again.
Uses DATABASE_URL, so point it at production to reset the live app.
"""
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from googleapiclient.discovery import build  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Order, RefundApproval, init_db, session, utcnow  # noqa: E402
from app.integrations.google import credentials  # noqa: E402

STATE = {  # order id: (status, ordered days ago, delivered days ago)
    "BRW-1042": ("shipped", 3, None),
    "BRW-1038": ("delivered", 9, 5),
    "BRW-0911": ("delivered", 70, 65),
    "BRW-1047": ("processing", 20 / 24, None),
}

s = get_settings()
init_db()
db = session()
now = utcnow()
for order_id, (status, ordered, delivered) in STATE.items():
    o = db.get(Order, order_id)
    if o is None:
        sys.exit(f"{order_id} missing: start the app once so it seeds the demo data.")
    o.status, o.refunded_amount = status, 0
    o.ordered_at = now - timedelta(days=ordered)
    o.delivered_at = now - timedelta(days=delivered) if delivered is not None else None
expired = db.query(RefundApproval).filter(RefundApproval.status == "pending").update({"status": "expired"})
db.commit()
db.close()
print(f"Orders reset ({expired} pending approval(s) expired).")

if s.sheet_id:
    sheets = build("sheets", "v4", credentials=credentials(s), cache_discovery=False).spreadsheets()
    meta = sheets.get(spreadsheetId=s.sheet_id).execute()
    first = meta["sheets"][0]["properties"]["title"]
    rows = sheets.values().get(spreadsheetId=s.sheet_id, range=f"'{first}'!A2:I").execute().get("values", [])
    if rows:
        if not any(sh["properties"]["title"] == "Test runs" for sh in meta["sheets"]):
            sheets.batchUpdate(spreadsheetId=s.sheet_id, body={
                "requests": [{"addSheet": {"properties": {"title": "Test runs"}}}]}).execute()
        sheets.values().append(spreadsheetId=s.sheet_id, range="'Test runs'!A1", valueInputOption="RAW",
                               insertDataOption="INSERT_ROWS", body={"values": rows}).execute()
        sheets.values().clear(spreadsheetId=s.sheet_id, range=f"'{first}'!A2:I").execute()
    print(f"Sheet: moved {len(rows)} row(s) to 'Test runs'; '{first}' now has only the header.")
