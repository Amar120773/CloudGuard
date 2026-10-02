"""Configuration parsing.

Regression coverage for settings that are read from `.env`: pydantic-settings
JSON-decodes complex field types straight out of a dotenv file before any
validator runs, so a `List[str]` field would make the documented
`CORS_ORIGINS=a,b` form crash the app at import.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


class TestCorsOrigins:
    def test_parses_a_comma_separated_list(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "http://a.test,http://b.test")
        assert Settings().cors_origins == ["http://a.test", "http://b.test"]

    def test_tolerates_surrounding_whitespace(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", " http://a.test ,  http://b.test  ")
        assert Settings().cors_origins == ["http://a.test", "http://b.test"]

    def test_parses_a_json_array(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", '["http://a.test", "http://b.test"]')
        assert Settings().cors_origins == ["http://a.test", "http://b.test"]

    def test_empty_means_no_origins(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "")
        assert Settings().cors_origins == []

    def test_falls_back_to_localhost_defaults(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        origins = Settings().cors_origins
        assert "http://localhost:5173" in origins

    def test_malformed_json_degrades_to_csv_rather_than_raising(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "[not valid json")
        assert Settings().cors_origins == ["[not valid json"]


class TestDerivedSettings:
    def test_celery_urls_default_to_redis_url(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://example:6379/3")
        monkeypatch.setenv("CELERY_BROKER_URL", "")
        monkeypatch.setenv("CELERY_RESULT_BACKEND", "")

        settings = Settings()
        assert settings.broker_url == "redis://example:6379/3"
        assert settings.result_backend == "redis://example:6379/3"

    def test_explicit_broker_url_wins(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://cache:6379/0")
        monkeypatch.setenv("CELERY_BROKER_URL", "redis://broker:6379/1")
        assert Settings().broker_url == "redis://broker:6379/1"

    @pytest.mark.parametrize(
        "mode,expected",
        [("moto_inproc", True), ("moto_server", True), ("real", False)],
    )
    def test_uses_moto_reflects_cloud_mode(self, monkeypatch, mode, expected):
        monkeypatch.setenv("CLOUD_MODE", mode)
        assert Settings().uses_moto is expected

    def test_blank_endpoint_url_becomes_none(self, monkeypatch):
        monkeypatch.setenv("AWS_ENDPOINT_URL", "")
        assert Settings().boto_endpoint_url is None

    def test_endpoint_url_is_passed_through(self, monkeypatch):
        monkeypatch.setenv("AWS_ENDPOINT_URL", "http://moto:5000")
        assert Settings().boto_endpoint_url == "http://moto:5000"


class TestEnvExample:
    """.env.example must actually load - it is the documented starting point."""

    def test_example_file_exists(self):
        assert _env_example_path().is_file(), ".env.example is missing from the repo root"

    def test_every_documented_key_is_a_known_setting(self):
        known = set(Settings.model_fields)
        # Aliases and frontend-only (Vite) keys are legitimately not fields.
        aliases = {"cors_origins"}
        unknown = []

        for line in _env_example_path().read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip()
            if key.startswith("VITE_"):
                continue
            if key.lower() not in known and key.lower() not in aliases:
                unknown.append(key)

        assert not unknown, f".env.example documents unknown settings: {unknown}"

    def test_example_values_load_without_error(self, monkeypatch):
        """Every value in .env.example must parse, not just the keys."""
        for line in _env_example_path().read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip().startswith("VITE_"):
                continue
            monkeypatch.setenv(key.strip(), value.strip())

        settings = Settings()  # must not raise
        assert settings.cloud_mode in ("moto_inproc", "moto_server", "real")
        assert settings.cors_origins
        assert settings.monthly_budget > 0
        assert 0 < settings.anomaly_contamination <= 0.5


def _env_example_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env.example"
