from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./agentforge.db"
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
