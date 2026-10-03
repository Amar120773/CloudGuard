"""Deployment hardening from the production-readiness audit.

One class per finding:

* the public health endpoint and the logs never carry a Redis password;
* the rate limit cannot be dodged by rotating X-API-Key while auth is off;
* client addresses come from X-Forwarded-For only via a trusted proxy;
* the CORS preview pattern is scoped, and credentialed CORS is off;
* rediss:// URLs are made acceptable to Celery, and a broker that refuses a
  task is reported instead of the job quietly running on the web process;
* the dashboard is told the score threshold and interval width the models used.
"""
from __future__ import annotations

import logging
import os
import ssl
import sys
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.security_deps import (
    _client_identity,
    check_proxy_trust,
    forwarded_allow_ips,
)
from app.cache import Keys, cache
from app.config import Settings, settings
from app.connection_urls import (
    describe_url,
    redact_url,
    scrub_credentials,
    with_redis_tls_defaults,
)
from app.logging_config import RedactCredentialsFilter
from app.main import app
from app.schemas.task import TaskStatus, TaskType
from app.services import task_service
from app.services.task_service import TaskDispatchError

# Obviously fake. Port 6399 is unreachable on purpose (see conftest), so the
# broker probe fails fast instead of waiting on DNS or TLS.
PASSWORD = "not-a-real-Pa55word"
SECRET_URL = f"rediss://default:{PASSWORD}@127.0.0.1:6399/0"

WRITE_PATH = "/api/cloud/ingest"
API_KEY = "unit-test-key"


def _with_peer(asgi_app, host):
    """Pin the TCP peer address, the way uvicorn's socket layer sets it."""

    async def wrapped(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope, client=(host, 50000))
        await asgi_app(scope, receive, send)

    return wrapped


def _fake_submit(task_type, celery_task, kwargs=None):
    """Accept the task without running anything; rate limiting happens earlier."""
    return {
        "task_id": f"stub-{uuid.uuid4().hex[:8]}",
        "task_type": task_type,
        "status": TaskStatus.PENDING,
        "executor": "inline",
        "message": "stub",
    }


def _buckets():
    """Rate-limit identities that have been counted so far."""
    return sorted(
        key.split(":write:", 1)[1] for key in cache._memory.keys(Keys.RATE_LIMIT_PREFIX)
    )


@pytest.fixture
def limited(monkeypatch):
    """Two writes per window, no real task execution, clean counters."""
    cache.flush_namespace(Keys.RATE_LIMIT_PREFIX)
    monkeypatch.setattr(settings, "rate_limit_write_requests", 2)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)
    monkeypatch.setattr(task_service, "submit", _fake_submit)
    yield
    cache.flush_namespace(Keys.RATE_LIMIT_PREFIX)


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setattr(settings, "cloudguard_api_key", API_KEY)
    return {"X-API-Key": API_KEY}


# ==========================================================================
# Connection-URL helpers
# ==========================================================================
class TestConnectionUrlHelpers:
    @pytest.mark.parametrize(
        "url",
        [
            f"rediss://default:{PASSWORD}@redis.example.test:6380/0",
            f"redis://:{PASSWORD}@redis.example.test:6379/0",  # password, no username
            f"rediss://default:{PASSWORD}@[::1]:6380/0",  # IPv6 host
            f"redis://redis.example.test:6379/0?password={PASSWORD}",  # as a query parameter
            f"rediss://default:{PASSWORD}@redis.example.test:6380/0?ssl_cert_reqs=required",
            f"amqp://guest:{PASSWORD}@rabbit.example.test:5672//",
            f"default:{PASSWORD}@redis.example.test:6379",  # no scheme at all
            f"rediss://default:pa/ss@{PASSWORD}@redis.example.test:6380",  # unencoded / and @
        ],
    )
    def test_redacted_url_never_contains_the_password(self, url):
        redacted = redact_url(url)
        assert PASSWORD not in redacted
        assert "***" in redacted

    def test_redaction_keeps_where_the_connection_went(self):
        assert (
            redact_url(f"rediss://default:{PASSWORD}@redis.example.test:6380/0")
            == "rediss://***@redis.example.test:6380/0"
        )

    @pytest.mark.parametrize("url", ["redis://localhost:6379/0", "memory://"])
    def test_a_url_without_credentials_is_unchanged(self, url):
        assert redact_url(url) == url

    @pytest.mark.parametrize("url", ["", None])
    def test_empty_input_is_handled(self, url):
        assert redact_url(url) == ""

    def test_credentials_inside_free_text_are_scrubbed(self):
        text = f"Error 111 connecting to {SECRET_URL}. Connection refused."
        scrubbed = scrub_credentials(text)
        assert PASSWORD not in scrubbed
        assert "Connection refused." in scrubbed

    def test_describe_url_reveals_transport_only(self):
        assert describe_url(SECRET_URL) == {"transport": "rediss", "tls": True}
        assert describe_url("redis://cache:6379/0") == {"transport": "redis", "tls": False}
        assert describe_url("") == {"transport": None, "tls": False}


# ==========================================================================
# /api/health
# ==========================================================================
class TestHealthNeverLeaksSecrets:
    @pytest.fixture
    def secret_broker(self, monkeypatch):
        from app.api import health

        monkeypatch.setattr(settings, "redis_url", SECRET_URL)
        monkeypatch.setattr(settings, "celery_broker_url", SECRET_URL)
        monkeypatch.setattr(settings, "celery_result_backend", SECRET_URL)
        health.reset_ping_cache()
        yield
        health.reset_ping_cache()

    def _assert_clean(self, response):
        assert response.status_code == 200
        assert PASSWORD not in response.text
        # Not even the host: nothing a client does with this response needs it.
        assert "127.0.0.1:6399" not in response.text
        celery = next(d for d in response.json()["dependencies"] if d["name"] == "celery")
        assert celery["metadata"]["broker_transport"] == "rediss"
        assert celery["metadata"]["broker_tls"] is True
        return celery

    def test_broker_unreachable(self, client, secret_broker):
        celery = self._assert_clean(client.get("/api/health"))
        assert celery["metadata"]["workers"] == 0

    def test_broker_up_without_workers(self, client, secret_broker, monkeypatch):
        celery = self._ping_returning(client, monkeypatch, replies=[])
        assert "queued tasks wait" in celery["detail"]

    def test_broker_up_with_a_worker(self, client, secret_broker, monkeypatch):
        celery = self._ping_returning(client, monkeypatch, replies=[{"celery@w1": {"ok": "pong"}}])
        assert celery["healthy"] is True
        assert celery["metadata"]["workers"] == 1

    def test_ping_failure(self, client, secret_broker, monkeypatch):
        celery = self._ping_returning(client, monkeypatch, raises=ValueError(SECRET_URL))
        assert celery["healthy"] is False

    def _ping_returning(self, client, monkeypatch, replies=None, raises=None):
        from app.workers import celery_app as celery_module

        class FakeControl:
            def ping(self, timeout=1.0, limit=None):
                if raises:
                    raise raises
                return replies

        monkeypatch.setattr(task_service, "broker_available", lambda force=False: True)
        monkeypatch.setattr(celery_module.celery_app, "control", FakeControl())
        return self._assert_clean(client.get("/api/health"))


# ==========================================================================
# Logs
# ==========================================================================
class TestLogsNeverLeakSecrets:
    def test_redis_connection_log_is_redacted(self, monkeypatch, caplog):
        from app.cache import CacheBackend

        class FakeRedis:
            def ping(self):
                return True

        backend = CacheBackend()
        monkeypatch.setattr(settings, "redis_url", SECRET_URL)
        monkeypatch.setattr(backend, "_connect", lambda: FakeRedis())

        with caplog.at_level(logging.INFO, logger="app.cache"):
            assert backend._redis() is not None

        assert "Redis cache connected at rediss://***@127.0.0.1:6399/0" in caplog.text
        assert PASSWORD not in caplog.text

    def test_inline_fallback_log_is_redacted(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "celery_broker_url", SECRET_URL)
        monkeypatch.setattr(task_service, "broker_available", lambda force=False: False)

        class Noop:
            def run(self, cg_task_id=None, **kwargs):
                return None

        with caplog.at_level(logging.WARNING, logger="app.services.task_service"):
            task_service.submit(TaskType.INGEST_CLOUD, Noop(), {})
        task_service.wait_for_inline(timeout=30)

        assert "running inline" in caplog.text
        assert PASSWORD not in caplog.text

    def test_dispatch_refusal_log_is_redacted(self, monkeypatch, caplog):
        monkeypatch.setattr(task_service, "broker_available", lambda force=False: True)

        with caplog.at_level(logging.ERROR, logger="app.services.task_service"):
            with pytest.raises(TaskDispatchError):
                task_service.submit(
                    TaskType.INGEST_CLOUD, _RefusingTask(ValueError(f"bad URL {SECRET_URL}")), {}
                )

        assert "refused" in caplog.text
        assert PASSWORD not in caplog.text

    def test_handler_filter_scrubs_library_messages(self):
        """Backstop for text no call site controls, e.g. a library's own log line."""
        record = logging.LogRecord(
            "kombu", logging.ERROR, __file__, 1, "Cannot connect to %s", (SECRET_URL,), None
        )
        assert RedactCredentialsFilter().filter(record) is True
        assert PASSWORD not in record.getMessage()

    def test_handler_filter_scrubs_tracebacks(self):
        try:
            raise ConnectionError(f"Error connecting to {SECRET_URL}")
        except ConnectionError:
            record = logging.LogRecord(
                "redis", logging.ERROR, __file__, 1, "boom", None, sys.exc_info()
            )
        RedactCredentialsFilter().filter(record)
        assert "Error connecting to rediss://***@" in record.exc_text
        assert PASSWORD not in record.exc_text

    def test_the_filter_is_installed_on_the_root_handler(self):
        root = logging.getLogger()
        assert any(
            isinstance(f, RedactCredentialsFilter) for h in root.handlers for f in h.filters
        )


# ==========================================================================
# Rate-limit identity
# ==========================================================================
class TestRateLimitIdentity:
    def test_auth_off_rotating_keys_still_share_the_ip_bucket(self, limited):
        """The audit's bypass: a fresh random key per request used to mean a fresh bucket."""
        client = TestClient(_with_peer(app, "203.0.113.10"))
        codes = [
            client.post(WRITE_PATH, headers={"X-API-Key": uuid.uuid4().hex}).status_code
            for _ in range(5)
        ]
        assert codes == [202, 202, 429, 429, 429]
        assert _buckets() == ["ip:203.0.113.10"]

    def test_auth_off_distinct_clients_get_distinct_buckets(self, limited):
        first = TestClient(_with_peer(app, "203.0.113.10"))
        second = TestClient(_with_peer(app, "198.51.100.20"))

        assert [first.post(WRITE_PATH).status_code for _ in range(3)] == [202, 202, 429]
        # The first client exhausting its window does not touch the second's.
        assert [second.post(WRITE_PATH).status_code for _ in range(2)] == [202, 202]

    def test_auth_on_the_key_is_rate_limited(self, limited, secured):
        client = TestClient(_with_peer(app, "203.0.113.10"))
        codes = [client.post(WRITE_PATH, headers=secured).status_code for _ in range(3)]
        assert codes == [202, 202, 429]

    def test_auth_on_one_key_is_one_bucket_across_addresses(self, limited, secured):
        first = TestClient(_with_peer(app, "203.0.113.10"))
        second = TestClient(_with_peer(app, "198.51.100.20"))

        assert first.post(WRITE_PATH, headers=secured).status_code == 202
        assert second.post(WRITE_PATH, headers=secured).status_code == 202
        # Changing address does not buy a key a second allowance.
        assert first.post(WRITE_PATH, headers=secured).status_code == 429
        assert second.post(WRITE_PATH, headers=secured).status_code == 429

    def test_auth_on_other_keys_are_rejected_and_never_get_a_bucket(self, limited, secured):
        client = TestClient(_with_peer(app, "203.0.113.10"))
        for _ in range(5):
            response = client.post(WRITE_PATH, headers={"X-API-Key": uuid.uuid4().hex})
            assert response.status_code == 403

        assert _buckets() == []
        # The genuine key's allowance is untouched by the rejected attempts.
        codes = [client.post(WRITE_PATH, headers=secured).status_code for _ in range(3)]
        assert codes == [202, 202, 429]

    def test_a_non_ascii_key_is_a_403_not_a_server_error(self, limited, secured):
        client = TestClient(_with_peer(app, "203.0.113.10"))
        response = client.post(WRITE_PATH, headers={"X-API-Key": "clé".encode("latin-1")})
        assert response.status_code == 403

    def test_identity_ignores_an_unverified_key(self):
        request = Request({"type": "http", "client": ("203.0.113.10", 1), "headers": []})
        assert _client_identity(request, "anything-at-all") == "ip:203.0.113.10"


# ==========================================================================
# Client IPs behind a proxy (uvicorn's ProxyHeadersMiddleware, as deployed)
# ==========================================================================
class TestTrustedProxies:
    PROXY_RANGE = "100.64.0.0/10"  # e.g. Railway's edge proxies

    def _behind(self, peer, trusted):
        return TestClient(_with_peer(ProxyHeadersMiddleware(app, trusted_hosts=trusted), peer))

    def test_client_address_comes_from_a_trusted_proxy(self, limited):
        client = self._behind("100.64.0.2", self.PROXY_RANGE)
        client.post(WRITE_PATH, headers={"X-Forwarded-For": "203.0.113.7"})
        assert _buckets() == ["ip:203.0.113.7"]

    def test_visitors_behind_one_proxy_are_limited_separately(self, limited):
        client = self._behind("100.64.0.2", self.PROXY_RANGE)
        visitor_a = {"X-Forwarded-For": "203.0.113.7"}
        visitor_b = {"X-Forwarded-For": "198.51.100.9"}

        assert [client.post(WRITE_PATH, headers=visitor_a).status_code for _ in range(3)] == [
            202,
            202,
            429,
        ]
        assert client.post(WRITE_PATH, headers=visitor_b).status_code == 202

    def test_a_client_cannot_spoof_its_address_through_the_proxy(self, limited):
        """The proxy appends the real address; anything the client wrote sits to its left."""
        client = self._behind("100.64.0.2", self.PROXY_RANGE)
        for forged in ("1.2.3.4", "5.6.7.8", "100.64.9.9"):
            client.post(WRITE_PATH, headers={"X-Forwarded-For": f"{forged}, 203.0.113.7"})
        assert _buckets() == ["ip:203.0.113.7"]

    def test_the_header_is_ignored_from_an_untrusted_peer(self, limited):
        client = self._behind("198.51.100.50", self.PROXY_RANGE)
        client.post(WRITE_PATH, headers={"X-Forwarded-For": "1.2.3.4"})
        assert _buckets() == ["ip:198.51.100.50"]

    def test_the_default_collapses_proxied_visitors_into_one_bucket(self, limited):
        """Why FORWARDED_ALLOW_IPS has to be set: the default trusts only 127.0.0.1."""
        client = self._behind("100.64.0.2", "127.0.0.1")
        client.post(WRITE_PATH, headers={"X-Forwarded-For": "203.0.113.7"})
        client.post(WRITE_PATH, headers={"X-Forwarded-For": "198.51.100.9"})
        assert _buckets() == ["ip:100.64.0.2"]

    def test_listed_app_addresses_are_skipped(self, limited):
        """Fly.io appends the app's own IP last; listing it lets the walk reach the client."""
        client = self._behind("10.0.0.5", "10.0.0.0/8,203.0.113.200")
        client.post(
            WRITE_PATH, headers={"X-Forwarded-For": "1.2.3.4, 198.51.100.9, 203.0.113.200"}
        )
        assert _buckets() == ["ip:198.51.100.9"]

    def test_wildcard_trust_lets_the_client_choose(self, limited):
        """Pins the claim behind the startup warning: with '*' the forged entry wins."""
        client = self._behind("100.64.0.2", "*")
        client.post(WRITE_PATH, headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.7"})
        assert _buckets() == ["ip:1.2.3.4"]


class TestProxyTrustStartupCheck:
    def test_wildcard_is_flagged(self, monkeypatch, caplog):
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
        with caplog.at_level(logging.WARNING, logger="app.api.security_deps"):
            assert check_proxy_trust() is False
        assert "pick its own address" in caplog.text

    def test_explicit_ranges_pass(self, monkeypatch):
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", "100.64.0.0/10")
        assert check_proxy_trust() is True

    def test_default_matches_uvicorn(self, monkeypatch):
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
        assert forwarded_allow_ips() == "127.0.0.1"


# ==========================================================================
# CORS
# ==========================================================================
# A stand-in for the project's real Vercel scope, which lives in the Vercel
# dashboard rather than in this repository.
SCOPE = "acme-team"
PREVIEW_REGEX = rf"https://cloudguard-dashboard-git-[a-z0-9-]+-{SCOPE}\.vercel\.app"
PRODUCTION_ORIGIN = "https://cloudguard-dashboard.vercel.app"


class TestCorsPolicy:
    @pytest.fixture
    def cors(self, monkeypatch):
        from starlette.middleware.cors import CORSMiddleware
        from starlette.responses import PlainTextResponse

        from app.main import cors_options

        monkeypatch.setattr(settings, "cors_origins_raw", PRODUCTION_ORIGIN)
        monkeypatch.setattr(settings, "cors_origin_regex", PREVIEW_REGEX)
        return TestClient(CORSMiddleware(PlainTextResponse("ok"), **cors_options()))

    @pytest.mark.parametrize(
        "origin",
        [
            PRODUCTION_ORIGIN,
            f"https://cloudguard-dashboard-git-main-{SCOPE}.vercel.app",
            f"https://cloudguard-dashboard-git-feature-login-{SCOPE}.vercel.app",
        ],
    )
    def test_project_origins_are_accepted(self, cors, origin):
        response = cors.get("/", headers={"Origin": origin})
        assert response.headers["access-control-allow-origin"] == origin

    @pytest.mark.parametrize(
        "origin",
        [
            # The audit's example: someone else's project with a lookalike name.
            "https://cloudguard-dashboard-git-evil.vercel.app",
            "https://cloudguard-dashboard-git-main-other-team.vercel.app",
            f"https://cloudguard-dashboard-git-main-{SCOPE}.vercel.app.evil.test",
            f"http://cloudguard-dashboard-git-main-{SCOPE}.vercel.app",
            "https://evil.example",
        ],
    )
    def test_unrelated_origins_are_rejected(self, cors, origin):
        response = cors.get("/", headers={"Origin": origin})
        assert "access-control-allow-origin" not in response.headers

    def test_credentialed_cors_is_disabled(self, cors):
        simple = cors.get("/", headers={"Origin": PRODUCTION_ORIGIN})
        preflight = cors.options(
            "/",
            headers={
                "Origin": PRODUCTION_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-API-Key",
            },
        )
        assert preflight.status_code == 200
        for response in (simple, preflight):
            assert "access-control-allow-credentials" not in response.headers

    def test_the_running_app_has_credentials_off(self, client):
        response = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert "access-control-allow-credentials" not in response.headers


# ==========================================================================
# Managed Redis over TLS
# ==========================================================================
class TestRedisTlsForCelery:
    def test_rediss_gains_ssl_cert_reqs(self):
        assert (
            with_redis_tls_defaults("rediss://:pw@h:6380/0")
            == "rediss://:pw@h:6380/0?ssl_cert_reqs=required"
        )

    def test_existing_parameters_are_kept(self):
        assert (
            with_redis_tls_defaults("rediss://h:6380/0?socket_timeout=5")
            == "rediss://h:6380/0?socket_timeout=5&ssl_cert_reqs=required"
        )

    @pytest.mark.parametrize("value", ["required", "CERT_REQUIRED", "none"])
    def test_an_explicit_choice_is_never_duplicated_or_overridden(self, value):
        url = f"rediss://h:6380/0?ssl_cert_reqs={value}"
        assert with_redis_tls_defaults(url) == url

    @pytest.mark.parametrize(
        "url", ["redis://h:6379/0", "redis://:pw@h:6379/0?db=1", "memory://", "", "amqps://h//"]
    )
    def test_other_urls_pass_through(self, url):
        assert with_redis_tls_defaults(url) == url

    def test_settings_apply_it_to_broker_and_result_backend(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "rediss://default:pw@h:6380/0")
        monkeypatch.setenv("CELERY_BROKER_URL", "")
        monkeypatch.setenv("CELERY_RESULT_BACKEND", "")
        configured = Settings()
        expected = "rediss://default:pw@h:6380/0?ssl_cert_reqs=required"
        assert configured.broker_url == expected
        assert configured.result_backend == expected

    @pytest.fixture
    def no_celery_env(self, monkeypatch):
        """Celery prefers these variables over the URLs an app is built with."""
        for name in (
            "CELERY_BROKER_URL",
            "CELERY_RESULT_BACKEND",
            "CELERY_BROKER_READ_URL",
            "CELERY_BROKER_WRITE_URL",
        ):
            monkeypatch.delenv(name, raising=False)

    def test_celery_accepts_the_normalised_url(self, no_celery_env):
        """The real failure: Celery refused the raw rediss:// URL outright."""
        from celery import Celery

        raw = "rediss://default:pw@127.0.0.1:6399/0"
        with pytest.raises(ValueError, match="ssl_cert_reqs"):
            Celery("tls-probe", broker=raw, backend=raw).backend

        fixed = with_redis_tls_defaults(raw)
        celery = Celery("tls-probe", broker=fixed, backend=fixed)
        assert type(celery.backend).__name__ == "RedisBackend"
        # Both halves verify the server certificate.
        assert celery.backend.connparams["ssl_cert_reqs"] == ssl.CERT_REQUIRED
        assert celery.connection_for_write().ssl["ssl_cert_reqs"] == ssl.CERT_REQUIRED

    def test_celerys_own_environment_variables_are_normalised(self, no_celery_env, monkeypatch):
        """CELERY_BROKER_URL / CELERY_RESULT_BACKEND outrank the URLs in code, so a
        raw rediss:// value there must be fixed in place, or Celery refuses it."""
        from celery import Celery

        from app.workers.celery_app import normalise_celery_environment

        raw = "rediss://default:pw@127.0.0.1:6399/0"
        monkeypatch.setenv("CELERY_BROKER_URL", raw)
        monkeypatch.setenv("CELERY_RESULT_BACKEND", raw)
        monkeypatch.setenv("CELERY_BROKER_WRITE_URL", "redis://plain:6379/0")

        normalise_celery_environment()

        assert os.environ["CELERY_BROKER_URL"] == f"{raw}?ssl_cert_reqs=required"
        assert os.environ["CELERY_RESULT_BACKEND"] == f"{raw}?ssl_cert_reqs=required"
        assert os.environ["CELERY_BROKER_WRITE_URL"] == "redis://plain:6379/0"
        # An app built with *no* URLs resolves them from the environment.
        backend = Celery("env-probe").backend
        assert backend.connparams["ssl_cert_reqs"] == ssl.CERT_REQUIRED


class _RefusingTask:
    """A Celery task whose submission fails, and which must never run inline."""

    def __init__(self, exc):
        self.exc = exc

    def apply_async(self, kwargs=None):
        raise self.exc

    def run(self, **kwargs):  # pragma: no cover - reaching this is the bug
        raise AssertionError("a refused task must not run on the API process")


class TestDispatchFailuresAreNotHiddenInline:
    def test_a_configuration_error_is_raised(self, monkeypatch):
        monkeypatch.setattr(task_service, "broker_available", lambda force=False: True)
        with pytest.raises(TaskDispatchError):
            task_service.submit(
                TaskType.INGEST_CLOUD,
                _RefusingTask(ValueError("A rediss:// URL must have parameter ssl_cert_reqs")),
                {},
            )

    def test_a_reachable_broker_rejecting_the_connection_is_raised(self, monkeypatch):
        """Wrong credentials look like a connection error, but the socket is up."""
        from kombu.exceptions import OperationalError

        monkeypatch.setattr(task_service, "broker_available", lambda force=False: True)
        with pytest.raises(TaskDispatchError):
            task_service.submit(
                TaskType.INGEST_CLOUD,
                _RefusingTask(OperationalError("invalid username-password pair")),
                {},
            )

    def test_a_broker_that_vanished_mid_dispatch_still_falls_back(self, monkeypatch):
        from kombu.exceptions import OperationalError

        probes = iter([True, False])  # up for the first probe, gone on the re-check
        monkeypatch.setattr(task_service, "broker_available", lambda force=False: next(probes))
        ran = {}

        class VanishingBroker:
            def apply_async(self, kwargs=None):
                raise OperationalError("Error 111 connecting. Connection refused.")

            def run(self, cg_task_id=None, **kwargs):
                ran["task_id"] = cg_task_id

        submission = task_service.submit(TaskType.INGEST_CLOUD, VanishingBroker(), {})
        assert submission["executor"] == "inline"
        assert task_service.wait_for_inline(timeout=30)
        assert ran["task_id"] == submission["task_id"]

    def test_the_api_reports_it_as_503(self, client, monkeypatch):
        def refuse(*args, **kwargs):
            raise TaskDispatchError("Celery refused the ingest_cloud_data task (ValueError)")

        monkeypatch.setattr(task_service, "submit", refuse)
        response = client.post(WRITE_PATH)
        assert response.status_code == 503
        assert response.json()["error_code"] == "task_dispatch_failed"


# ==========================================================================
# Model configuration the dashboard renders
# ==========================================================================
class TestModelConfigurationIsReported:
    def test_posture_reports_the_threshold_it_applied(self, warm_client):
        body = warm_client.get("/api/security").json()
        assert body["score_threshold"] == settings.anomaly_score_threshold

    def test_forecast_reports_its_interval_width(self, warm_client):
        body = warm_client.get("/api/costs/forecast").json()
        assert body["interval_width"] == settings.prophet_interval_width

    def test_the_dashboard_payload_carries_both(self, warm_client):
        body = warm_client.get("/api/dashboard").json()
        assert body["security"]["score_threshold"] == settings.anomaly_score_threshold
        assert body["forecast"]["interval_width"] == settings.prophet_interval_width

    def test_a_custom_threshold_is_what_gets_reported(self, security_records):
        from app.ml.anomaly_detection import SecurityAnomalyDetector

        result = SecurityAnomalyDetector(score_threshold=72.5).detect(security_records)
        assert result["score_threshold"] == 72.5

    def test_a_custom_interval_is_what_gets_reported(self, cost_records, monkeypatch):
        from app.ml.forecasting import CostForecaster

        monkeypatch.setattr(settings, "prophet_interval_width", 0.9)
        payload = CostForecaster(horizon_days=14).forecast(cost_records, run_backtest=False)
        assert payload["interval_width"] == 0.9


# ==========================================================================
# Shared demo data: test-anomaly injection
# ==========================================================================
class TestAnomalyInjectionControl:
    @pytest.mark.parametrize(
        "environment,explicit,expected",
        [
            ("production", None, False),  # a public deployment is safe by default
            ("Production ", None, False),
            ("development", None, True),  # local demos keep the feature
            ("test", None, True),
            ("production", "true", True),  # an explicit choice always wins
            ("development", "false", False),
            ("production", "", False),  # a blank dashboard field means unset
        ],
    )
    def test_default_and_overrides(self, monkeypatch, environment, explicit, expected):
        monkeypatch.setenv("ENVIRONMENT", environment)
        if explicit is None:
            monkeypatch.delenv("ALLOW_ANOMALY_INJECTION", raising=False)
        else:
            monkeypatch.setenv("ALLOW_ANOMALY_INJECTION", explicit)
        assert Settings().anomaly_injection_enabled is expected

    @pytest.fixture
    def injection_off(self, monkeypatch):
        cache.flush_namespace(Keys.RATE_LIMIT_PREFIX)
        monkeypatch.setattr(settings, "allow_anomaly_injection", False)
        monkeypatch.setattr(task_service, "submit", _fake_submit)
        yield
        cache.flush_namespace(Keys.RATE_LIMIT_PREFIX)

    @pytest.mark.parametrize("path", ["/api/security/anomalies/run", "/api/security/ingest"])
    def test_injection_is_refused_when_disabled(self, client, injection_off, path):
        response = client.post(path, json={"inject_anomaly": True})
        assert response.status_code == 403
        assert response.json()["detail"]["error_code"] == "anomaly_injection_disabled"

    @pytest.mark.parametrize("path", ["/api/security/anomalies/run", "/api/security/ingest"])
    def test_ordinary_runs_still_work_when_disabled(self, client, injection_off, path):
        assert client.post(path, json={"force": True}).status_code == 202

    def test_injection_is_accepted_when_enabled(self, client, monkeypatch):
        cache.flush_namespace(Keys.RATE_LIMIT_PREFIX)
        monkeypatch.setattr(settings, "allow_anomaly_injection", True)
        monkeypatch.setattr(task_service, "submit", _fake_submit)
        response = client.post("/api/security/anomalies/run", json={"inject_anomaly": True})
        assert response.status_code == 202

    @pytest.mark.parametrize("allowed", [True, False])
    def test_the_dashboard_tells_the_ui(self, client, monkeypatch, allowed):
        monkeypatch.setattr(settings, "allow_anomaly_injection", allowed)
        body = client.get("/api/dashboard").json()
        assert body["features"] == {"anomaly_injection": allowed}
