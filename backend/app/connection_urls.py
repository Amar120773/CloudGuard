"""Connection URLs that may carry credentials.

A managed Redis URL looks like ``rediss://default:<password>@host:6379``, so it
must never reach a log line, an API response or a task record as-is. Every place
that needs to mention one goes through this module rather than through ad-hoc
string slicing:

* :func:`redact_url` - for logs. Keeps scheme, host, port and path so the line
  still says *where* a connection went; masks the userinfo and any
  credential-like query parameter.
* :func:`describe_url` - for public responses. Transport and TLS only; not even
  the host leaves the process.
* :func:`scrub_credentials` - for free text that may *contain* a URL, such as an
  exception message about to be logged or stored.
* :func:`with_redis_tls_defaults` - makes a ``rediss://`` URL acceptable to
  Celery, which otherwise refuses it.

This module must not import ``app.config``: the settings depend on it.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit

REDACTED = "***"

# Schemes that mean "TLS" for the brokers CloudGuard can talk to.
_TLS_SCHEMES = frozenset({"rediss", "amqps"})

# "scheme://<userinfo>@". \S* is greedy, so the match runs to the *last* "@" in
# the token: an unencoded "@" or "/" inside a password cannot leave a fragment of
# it behind. Over-masking an exotic path is the safe direction to err in.
_USERINFO = re.compile(r"(?P<scheme>\b[A-Za-z][A-Za-z0-9+.-]*://)\S*@")

# "?password=..." / "&token=..." - redis-py, for one, accepts credentials as
# query parameters.
_SECRET_QUERY_PARAM = re.compile(
    r"(?P<name>[?&][\w.-]*(?:pass|pwd|secret|token|key|auth|cred)[\w.-]*=)[^&\s#]*",
    re.IGNORECASE,
)


def scrub_credentials(text: Optional[str]) -> str:
    """Mask every URL credential embedded anywhere in ``text``."""
    if not text:
        return text or ""
    text = _USERINFO.sub(lambda match: f"{match.group('scheme')}{REDACTED}@", text)
    return _SECRET_QUERY_PARAM.sub(lambda match: f"{match.group('name')}{REDACTED}", text)


def redact_url(url: Optional[str]) -> str:
    """``url`` with its credentials masked, safe for a log line."""
    if not url:
        return ""
    text = str(url).strip()
    if "://" not in text and "@" in text:
        # Not a URL we can reason about ("user:pass@host"); keep only what
        # follows the last "@" rather than guess where the secret ends.
        text = f"{REDACTED}@{text.rsplit('@', 1)[1]}"
    return scrub_credentials(text)


def describe_url(url: Optional[str]) -> Dict[str, Any]:
    """Public-safe summary of a connection URL: its transport and whether it is TLS."""
    try:
        scheme = urlsplit(str(url or "").strip()).scheme.lower()
    except ValueError:
        scheme = ""
    return {"transport": scheme or None, "tls": scheme in _TLS_SCHEMES}


def with_redis_tls_defaults(url: str) -> str:
    """Make a ``rediss://`` URL acceptable to Celery.

    Celery's Redis result backend refuses ``rediss://`` unless the URL says how
    to verify the server certificate ("A rediss:// URL must have parameter
    ssl_cert_reqs ..."). The API used to catch that error and run the job on the
    web process instead. Default it to ``required`` - verify the certificate,
    which is what a managed Redis over TLS expects; lower-case is the spelling
    both Celery and redis-py accept.

    An explicit ``ssl_cert_reqs`` is left alone, every other parameter is kept
    in place, and anything that is not ``rediss://`` passes through unchanged.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url  # malformed: let Celery report it in its own words
    if parts.scheme.lower() != "rediss":
        return url
    if any(name == "ssl_cert_reqs" for name, _ in parse_qsl(parts.query, keep_blank_values=True)):
        return url
    query = f"{parts.query}&ssl_cert_reqs=required" if parts.query else "ssl_cert_reqs=required"
    return urlunsplit(parts._replace(query=query))
