"""Public home, privacy and terms pages (required by Google's OAuth consent screen)."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_STYLE = """
body{font:16px/1.6 system-ui,sans-serif;max-width:680px;margin:40px auto;padding:0 16px;color:#1d2433;background:#fff}
h1{font-size:1.6rem}h2{font-size:1.1rem;margin-top:1.6em}a{color:#2563eb}
@media (prefers-color-scheme:dark){body{background:#0b1124;color:#e2e8f0}a{color:#7aa2ff}}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{title}</title>"
        f"<style>{_STYLE}</style></head><body>{body}"
        f"<p style='margin-top:3em;font-size:.9rem'><a href='/'>Home</a> · <a href='/privacy'>Privacy</a> · "
        f"<a href='/terms'>Terms</a></p></body></html>"
    )


@router.get("/", include_in_schema=False)
def home():
    return _page("Brewline Support Agent", """
<h1>Brewline Support Agent</h1>
<p>A demo AI agent that handles the support inbox of <strong>Brewline Coffee Co.</strong>, a fictional store.
It reads incoming support emails, answers routine questions from order data and store policy, sends refund
requests to a person in Slack for approval, escalates sensitive emails, and logs every email to a Google Sheet.</p>
<p>This is a portfolio project by Srinivas Thomala. The store, its customers and its orders are demo data.</p>
<p>Contact: brewline.support@gmail.com</p>
""")


@router.get("/privacy", include_in_schema=False)
def privacy():
    return _page("Privacy Policy", """
<h1>Privacy Policy</h1>
<p>Last updated: November 6, 2026</p>
<p>Brewline Support Agent is a demo application. It connects to a single Gmail account owned by the operator
(brewline.support@gmail.com) and to a Google Sheet owned by the same account.</p>
<h2>What data it accesses</h2>
<p>Using the Gmail API, it reads emails sent to the support inbox, sends replies in the same thread, and adds
a label to emails it has processed. Using the Google Sheets API, it appends one row per processed email
(time, sender, subject, category, action and a short summary).</p>
<h2>How the data is used</h2>
<p>Email content is sent to Anthropic's Claude API to decide how to answer, and short summaries may be posted to
the operator's private Slack workspace. Data is used only to answer and log support emails. It is not sold,
shared for advertising, or used to train models.</p>
<h2>Storage and retention</h2>
<p>Processing records are kept in the application's database and the operator's Google Sheet for the life of
the demo, and can be deleted on request.</p>
<h2>Google user data</h2>
<p>The use of information received from Google APIs adheres to the
<a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services User Data Policy</a>,
including the Limited Use requirements.</p>
<h2>Contact</h2>
<p>brewline.support@gmail.com</p>
""")


@router.get("/terms", include_in_schema=False)
def terms():
    return _page("Terms of Service", """
<h1>Terms of Service</h1>
<p>Last updated: November 6, 2026</p>
<p>Brewline Support Agent is a demonstration project. Brewline Coffee Co. is a fictional store, and no real
products are sold or refunds paid. The service is provided as is, without warranties, and may change or stop
at any time.</p>
<p>Questions: brewline.support@gmail.com</p>
""")
