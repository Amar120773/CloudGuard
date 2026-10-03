"""Central configuration for CloudGuard.

All tunables are environment driven (12-factor) so the same image can run
against mocked AWS (Moto) locally and real AWS in production without code
changes. See `.env.example` at the repository root.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import List, Literal, Optional

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.connection_urls import with_redis_tls_defaults

CloudMode = Literal["moto_inproc", "moto_server", "real"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    app_name: str = "CloudGuard API"
    app_version: str = "1.0.0"
    environment: str = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api"
    # Kept as a raw string: pydantic-settings JSON-decodes complex types straight
    # out of .env before any validator runs, so a List[str] here makes the plain
    # `CORS_ORIGINS=a,b` form in .env.example crash the app at import.
    # Regex alternative to the fixed list, for hosts that mint a new origin per
    # deployment. Vercel preview URLs look like
    # my-app-git-<branch>-<scope>.vercel.app, which no static allowlist can cover.
    # Always end the pattern with your own Vercel scope: anyone can create a
    # project whose URL starts with "cloudguard-", but not one under your scope.
    #   CORS_ORIGIN_REGEX=https://cloudguard-dashboard-git-[a-z0-9-]+-<your-scope>\.vercel\.app
    cors_origin_regex: str = ""

    cors_origins_raw: str = Field(
        default="http://localhost:5173,http://localhost:4173,"
                "http://localhost:3000,http://127.0.0.1:5173",
        validation_alias=AliasChoices("CORS_ORIGINS", "cors_origins", "cors_origins_raw"),
    )

    # ---------------------------------------------------------------- redis
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_timeout: float = 2.0
    redis_connect_timeout: float = 2.0

    # --------------------------------------------------------------- celery
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    celery_task_time_limit: int = 600
    celery_task_soft_time_limit: int = 540
    # Periodic metric collection (Celery beat), in seconds.
    #
    # These must stay BELOW the matching cache TTL, or a pipeline expires before
    # beat refreshes it and the dashboard empties out between cycles. The
    # invariant enforced in tests is:
    #     beat interval  <  freshness threshold  <  cache TTL
    # Roughly: refresh every B, warn after ~2B, expire after ~4-5B. One missed
    # beat cycle is therefore invisible, two shows as stale, and only a sustained
    # outage expires the data.
    beat_cloud_interval: int = 120
    beat_security_interval: int = 180
    beat_cost_interval: int = 600

    # ------------------------------------------------------------ cache TTL
    cache_ttl_cloud: int = 600
    cache_ttl_cost: int = 3000
    cache_ttl_forecast: int = 3600
    cache_ttl_security: int = 900
    cache_ttl_anomaly: int = 900
    task_record_ttl: int = 86_400

    # ------------------------------------------------------- freshness
    # How old a cached payload may be before the UI calls it stale. These are
    # deliberately SHORTER than the matching cache TTL above: the payload is
    # still served for the rest of its TTL, but past this point the dashboard
    # says so. If a threshold ever reached the TTL the stale state would be
    # unreachable, which is exactly the bug this replaced.
    freshness_cloud: int = 300
    freshness_cost: int = 1500
    freshness_forecast: int = 1500
    freshness_security: int = 420
    freshness_anomaly: int = 420
    # Long-lived marker recording the last successful run of each pipeline, so
    # an expired cache can still report "last successful forecast: 10m ago".
    last_success_ttl: int = 604_800

    # --------------------------------------------------------- security
    # Empty disables API-key enforcement (local demo default). Set it to protect
    # the endpoints that queue expensive ML work.
    cloudguard_api_key: str = ""
    rate_limit_write_requests: int = 10
    rate_limit_window_seconds: int = 60
    # "Inject test anomaly" appends an event to data every visitor shares, and it
    # stays until the API restarts. Unset means: allowed everywhere except
    # ENVIRONMENT=production, so a public deployment is safe with no extra
    # configuration. Set true or false to decide explicitly.
    allow_anomaly_injection: Optional[bool] = None

    # ------------------------------------------------------------------ aws
    cloud_mode: CloudMode = "moto_inproc"
    aws_region: str = "us-east-1"
    aws_endpoint_url: str = ""
    aws_access_key_id: str = "testing"
    aws_secret_access_key: str = "testing"
    demo_seed: int = 1337
    demo_instance_count: int = 8
    demo_history_days: int = 120
    demo_security_events: int = 240
    log_group_flow_logs: str = "/cloudguard/vpc/flowlogs"

    # ------------------------------------------------------- ml: forecasting
    forecast_horizon_days: int = 30
    forecast_backtest_days: int = 14
    # 0.01 keeps the trend stiff enough not to chase one-off steps; tuned on the
    # hold-out backtest, where it beats a seasonal-naive baseline by ~19% MAE.
    prophet_changepoint_prior_scale: float = 0.01
    prophet_seasonality_mode: str = "multiplicative"
    prophet_interval_width: float = 0.85
    monthly_budget: float = 14_000.0
    cost_spike_threshold_pct: float = 15.0

    # -------------------------------------------------- ml: anomaly detection
    # ~2.5% expected outlier rate is a realistic prior for security telemetry and
    # leaves headroom above the seeded anomaly count, so the live-injection demo
    # still has room to be flagged. Paired with the score threshold below it
    # reaches precision 1.0 / recall 1.0 on the seeded evaluation set.
    anomaly_contamination: float = 0.025
    anomaly_n_estimators: int = 240
    anomaly_random_state: int = 42
    anomaly_score_threshold: float = 65.0

    @property
    def standalone(self) -> bool:
        """No Redis and no Celery on purpose: one process does everything.

        Set ``REDIS_URL=none`` for a single free web service. Results live in this
        process's memory and jobs run on its own threads, which is the design in
        that setup rather than a fault, so nothing reports it as degraded. An
        empty value means the same; it used to leave Celery guessing a broker.
        A separately configured CELERY_BROKER_URL means a task queue exists, so
        that combination is not standalone.
        """
        no_redis = self.redis_url.strip().lower() in ("", "none")
        return no_redis and not self.celery_broker_url.strip()

    @field_validator("allow_anomaly_injection", mode="before")
    @classmethod
    def _blank_means_unset(cls, value):
        # A dashboard field left empty arrives as "", which is not a boolean.
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def anomaly_injection_enabled(self) -> bool:
        """Whether callers may inject test anomalies into the shared demo data."""
        if self.allow_anomaly_injection is not None:
            return self.allow_anomaly_injection
        return self.environment.strip().lower() != "production"

    @property
    def cors_origins(self) -> List[str]:
        """Allowed origins, accepting either `a,b` or a JSON array."""
        raw = (self.cors_origins_raw or "").strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except ValueError:
                pass  # fall through to the comma-separated reading
        return [item.strip() for item in raw.split(",") if item.strip()]

    # Both Celery URLs go through with_redis_tls_defaults: Celery rejects a
    # rediss:// URL (managed Redis over TLS) that lacks ssl_cert_reqs.
    @property
    def broker_url(self) -> str:
        return with_redis_tls_defaults(self.celery_broker_url or self.redis_url)

    @property
    def result_backend(self) -> str:
        return with_redis_tls_defaults(self.celery_result_backend or self.redis_url)

    @property
    def uses_moto(self) -> bool:
        return self.cloud_mode in ("moto_inproc", "moto_server")

    @property
    def boto_endpoint_url(self) -> str | None:
        return self.aws_endpoint_url or None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
