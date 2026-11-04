from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # SQLite locally; Postgres (Neon) in production.
    database_url: str = "sqlite:///./support_agent.db"

    # Claude
    anthropic_api_key: str = ""
    agent_model: str = "claude-opus-5-5"
    agent_effort: str = "medium"
    agent_max_steps: int = 8
    agent_deadline_seconds: float = 60.0
    agent_max_output_tokens: int = 16000

    # Store identity used in replies (fictional demo business).
    store_name: str = "Brewline Coffee Co."
    support_signature: str = "Brewline Support"

    # Comma-separated real addresses you control, mapped in order onto the demo
    # customers (Maya, Leo, Sam, Ana), so test emails you send find their orders.
    demo_customer_emails: str = ""

    # Business rules enforced in code, not by the model.
    refund_window_days: int = 30
    max_emails_per_hour: int = 30  # circuit breaker against mail floods / loops

    # Gmail + Sheets (one Google OAuth client, refresh token from scripts/google_auth.py)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    gmail_poll_seconds: int = 20
    gmail_processed_label: str = "agent-processed"
    sheet_id: str = ""  # Google Sheet that receives one row per email

    # Slack (bot token + signing secret for the interactive Approve/Reject buttons)
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_channel_id: str = ""

    # Turn the Gmail poller on (off in tests and local dev unless configured).
    poller_enabled: bool = False

    @property
    def sync_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
