from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Settings come from (later wins): the repo-root .env, then apps/api/.env, then real environment variables.
# Absolute paths, so it works no matter which folder the API is started from.
_API_DIR = Path(__file__).resolve().parents[1]
ENV_FILES = (_API_DIR.parents[1] / ".env", _API_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, env_file_encoding="utf-8", extra="ignore")

    # development | production. production = refuse to start with unsafe settings (services/production.py),
    # hide /docs, and send HSTS. Keep development on your laptop.
    app_env: str = "development"
    database_url: str = "sqlite:///./agentforge.db"
    log_format: str = "text"  # text | json (structured logs for log pipelines)
    log_level: str = "INFO"
    db_auto_migrate: bool = True  # run Alembic migrations at startup (turn off if you migrate in CI/CD)
    db_pool_size: int = 20
    db_max_overflow: int = 30
    # Only these direct peers may set X-Forwarded-For (comma-separated IPs/hostnames
    # of your reverse proxy / load balancer). Empty = never trust the header.
    trusted_proxies: str = ""
    # LLM prices for cost tracking, USD per 1M tokens, as JSON:
    # {"openai/gpt-oss-120b": {"input": 0.15, "output": 0.75}}  <- check your provider's pricing page.
    # Models not listed are traced with cost_usd = null.
    model_prices: str = "{}"
    # OpenTelemetry: where POST /runs/{id}/trace/export sends OTLP/HTTP JSON traces, e.g.
    # http://localhost:4318/v1/traces (Jaeger / Grafana Tempo / Langfuse OTLP endpoint).
    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_headers: str = ""  # "Authorization=Basic xxx,x-tenant=abc"
    # --- Team & email (cluster 16) ---
    public_app_url: str = "http://localhost:3000"  # the web app; invite links point here
    # --- Company login (SSO) - PAUSED. The code stays (services/sso.py, routes/sso.py) but is switched off:
    # with SSO_ENABLED=false every /sso endpoint answers 404 and sign-in is plain email + password.
    # Set SSO_ENABLED=true (and NEXT_PUBLIC_SSO_ENABLED=true for the web app) when SSO work resumes.
    sso_enabled: bool = False
    # SSO step 1: DNS-over-HTTPS resolver used to check domain-ownership TXT records (JSON API)
    sso_dns_resolver_url: str = "https://cloudflare-dns.com/dns-query"
    invitation_ttl_hours: int = 24 * 7
    mail_backend: str = "console"  # console (log only) | smtp
    mail_from: str = "AgentForge <no-reply@localhost>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_ssl: bool = False  # true = implicit TLS (port 465); false = STARTTLS
    smtp_starttls: bool = True  # upgrade plain connections with STARTTLS (turn off only for local test servers)

    # --- Rate limits & quotas (cluster 13); 0 = unlimited. Operators override per workspace. ---
    rate_limit_fail_closed: bool = False         # production: refuse logins when Redis is down
    workspace_max_concurrent_runs: int = 20      # queued + running at the same time
    workspace_runs_per_day: int = 0
    workspace_tokens_per_minute: int = 0         # LLM input+output tokens
    workspace_tokens_per_day: int = 0

    # --- Files (cluster 19) ---
    storage_backend: str = "local"           # local | s3 (AWS S3, Cloudflare R2, MinIO, ...)
    storage_local_dir: str = "./storage"
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint_url: str = ""                # e.g. https://<account>.r2.cloudflarestorage.com or http://minio:9000
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    max_file_bytes: int = 25 * 1024 * 1024
    signed_url_ttl_seconds: int = 300
    # key for HMAC-signed local download/upload links (falls back to INTERNAL_API_TOKEN)
    file_signing_key: str = ""

    # --- Data retention (cluster 18); workspace owners can override per workspace ---
    retention_run_data_days: int = 30        # then run input/output/state/events are deleted ("compacted")
    retention_run_rows_days: int = 365       # then the run row itself is deleted
    retention_memory_days: int = 90          # chat memory idle this long is deleted
    retention_deleted_graph_days: int = 30   # restore window before a deleted graph is purged
    retention_audit_days: int = 365          # audit entries older than this are pruned (chain stays verifiable)
    retention_interval_hours: float = 6      # how often the cleanup job runs (0 = off)
    # How often the API repairs runs whose final status never arrived (0 = off).
    run_reconcile_interval_seconds: int = 30
    temporal_address: str = "localhost:7233"
    temporal_task_queue: str = "agentforge"
    cors_origins: str = "http://localhost:3000"
    copilot_api_url: str = ""
    copilot_api_key: str = ""
    copilot_model: str = "gpt-4o-mini"
    # Groq integration – will be used when Copilot settings are empty
    groq_api_key: str = ""
    groq_model: str = ""
    # Redis integration for distributed rate limiting
    redis_url: str = "redis://localhost:6379/0"
    # Supabase connection & API credentials
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # --- Auth (cluster 1/2) ---
    # When false, requests without a session still work on graphs that have no
    # workspace (legacy/single-user mode, keeps the current web app working).
    auth_required: bool = False
    session_ttl_hours: int = 24 * 7
    session_cookie_name: str = "af_session"
    session_cookie_secure: bool = False  # set true behind HTTPS

    # --- Worker <-> API (cluster 6/8/9) ---
    internal_api_token: str = ""
    public_base_url: str = "http://localhost:8000"

    # --- Credentials vault (cluster 9) ---
    # 32-byte key, urlsafe-base64. Generate: python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
    credentials_master_key: str = ""
    credentials_master_key_id: str = "k1"
    # Older keys still accepted for decryption during rotation: "k0:<key>,k-1:<key>"
    credentials_previous_keys: str = ""

    # --- OAuth providers (cluster 9) ---
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""
    oauth_slack_client_id: str = ""
    oauth_slack_client_secret: str = ""


    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
