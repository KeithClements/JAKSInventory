"""QBO OAuth 2.0 helper — native Qt-friendly flow.

Drives the Intuit authorization-code flow without depending on the Flask
webapp. Used by Settings → Integrations → QuickBooks Online → Connect.

Flow:
    1. ``start_oauth_flow()`` builds the Intuit consent URL with a CSRF
       ``state`` token and a redirect URI of ``http://127.0.0.1:8765/callback``.
    2. Spins up a one-shot ``http.server.HTTPServer`` on 127.0.0.1:8765 in
       a background thread.
    3. Opens the consent URL in the user's default browser.
    4. Blocks (with timeout, default 300s) until Intuit redirects back
       with ``code`` + ``realmId`` + ``state``.
    5. Exchanges the code for ``access_token`` + ``refresh_token`` via
       :mod:`intuitlib.client.AuthClient`.
    6. Writes the tokens atomically to ``qbo/.qbo_tokens.json`` and
       resets the cached :class:`qbo.client.QBOClient` singleton.

``disconnect()`` is the inverse — wipes the token file and clears
``QBO_REFRESH_TOKEN`` from ``.env``.

Both functions are safe to call from a worker thread; neither touches
Qt directly.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


# Intuit redirect URI must exactly match what's registered in the
# developer dashboard. We hard-code the port so the user only has to
# register one URI.
_OAUTH_HOST = "localhost"
_OAUTH_PORT = 8765
_REDIRECT_URI = f"http://{_OAUTH_HOST}:{_OAUTH_PORT}/callback"
_TOKEN_CACHE = Path(__file__).parent / ".qbo_tokens.json"
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


# ---------------------------------------------------------------------------
# Local callback HTTP server
# ---------------------------------------------------------------------------

class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the single GET to ``/callback`` and stashes the query."""

    # set by the caller via class attribute before request comes in
    captured: dict[str, str] | None = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Silence the default stderr access log — keeps the terminal clean.
        return

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        q = parse_qs(parsed.query)
        _CallbackHandler.captured = {k: v[0] for k, v in q.items() if v}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = (
            "<!doctype html><html><head><title>QuickBooks Connected</title>"
            "<style>body{font-family:sans-serif;padding:40px;"
            "text-align:center;color:#222;}h1{color:#2ecc71;}"
            "</style></head><body>"
            "<h1>&#10004; Connected</h1>"
            "<p>You can close this tab and return to JAK&rsquo;S Inventory.</p>"
            "</body></html>"
        )
        self.wfile.write(body.encode("utf-8"))


def _port_in_use(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        s.bind((host, port))
        s.close()
        return False
    except OSError:
        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_oauth_flow(
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
    environment: str | None = None,
    timeout: float = 300.0,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Run the full Intuit OAuth 2.0 authorization-code flow.

    Returns a dict::

        {"success": True, "realm_id": "...", "environment": "sandbox"}

    or, on failure::

        {"success": False, "error": "<reason>"}

    ``client_id`` / ``client_secret`` / ``environment`` default to the
    ``QBO_CLIENT_ID`` / ``QBO_CLIENT_SECRET`` / ``QBO_ENVIRONMENT`` env
    vars. The redirect URI used (``http://127.0.0.1:8765/callback``)
    **must be registered** in the Intuit developer dashboard.
    """
    client_id = (client_id or os.environ.get("QBO_CLIENT_ID", "")).strip()
    client_secret = (
        client_secret or os.environ.get("QBO_CLIENT_SECRET", "")
    ).strip()
    environment = (
        environment or os.environ.get("QBO_ENVIRONMENT", "sandbox")
    ).strip().lower() or "sandbox"

    if not client_id or not client_secret:
        return {
            "success": False,
            "error": "QBO_CLIENT_ID / QBO_CLIENT_SECRET not configured.",
        }

    try:
        from intuitlib.client import AuthClient
        from intuitlib.enums import Scopes
    except ImportError:
        return {
            "success": False,
            "error": (
                "intuitlib not installed. Run:\n"
                "    pip install intuit-oauth python-quickbooks"
            ),
        }

    if _port_in_use(_OAUTH_HOST, _OAUTH_PORT):
        return {
            "success": False,
            "error": (
                f"Port {_OAUTH_PORT} is already in use. Close whatever "
                "is bound to it (often another OAuth flow or a stale "
                "Python process) and try again."
            ),
        }

    auth_client = AuthClient(
        client_id=client_id,
        client_secret=client_secret,
        environment=environment,
        redirect_uri=_REDIRECT_URI,
    )

    state = secrets.token_urlsafe(16)
    try:
        auth_url = auth_client.get_authorization_url(
            [Scopes.ACCOUNTING], state_token=state,
        )
    except TypeError:
        # Older intuitlib versions don't accept state_token kwarg.
        auth_url = auth_client.get_authorization_url([Scopes.ACCOUNTING])

    # Reset any previous capture, then start the server.
    _CallbackHandler.captured = None
    try:
        server = HTTPServer((_OAUTH_HOST, _OAUTH_PORT), _CallbackHandler)
    except OSError as exc:
        return {
            "success": False,
            "error": f"Failed to bind {_OAUTH_HOST}:{_OAUTH_PORT}: {exc}",
        }

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        if open_browser:
            try:
                webbrowser.open(auth_url)
            except Exception as exc:
                logger.warning("[QBO] webbrowser.open failed: %s", exc)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _CallbackHandler.captured is not None:
                break
            time.sleep(0.1)
        else:
            return {
                "success": False,
                "error": (
                    f"OAuth timed out after {int(timeout)} s. "
                    "Click Connect again to retry."
                ),
            }
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass

    captured = dict(_CallbackHandler.captured or {})
    _CallbackHandler.captured = None

    if captured.get("error"):
        return {
            "success": False,
            "error": (
                f"Intuit reported an error: {captured['error']} "
                f"{captured.get('error_description', '')}"
            ).strip(),
        }

    code = captured.get("code")
    realm_id = captured.get("realmId") or captured.get("realm_id")
    returned_state = captured.get("state")

    if not code or not realm_id:
        return {
            "success": False,
            "error": "Callback missing 'code' or 'realmId'.",
        }
    if returned_state and returned_state != state:
        return {
            "success": False,
            "error": "CSRF state mismatch — possible interception. Aborted.",
        }

    # Exchange code for tokens.
    try:
        auth_client.get_bearer_token(code, realm_id=realm_id)
    except Exception as exc:
        return {"success": False, "error": f"Token exchange failed: {exc}"}

    payload = {
        "access_token": auth_client.access_token,
        "refresh_token": auth_client.refresh_token,
        "realm_id": str(realm_id),
        "environment": environment,
        "created_at": int(time.time()),
    }

    # Atomic write: tmp then rename.
    try:
        _TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _TOKEN_CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(_TOKEN_CACHE)
    except Exception as exc:
        return {"success": False, "error": f"Failed to save tokens: {exc}"}

    # Persist refresh token to .env so subsequent processes can boot
    # without needing a fresh OAuth round-trip.
    try:
        _write_env_kv("QBO_REFRESH_TOKEN", auth_client.refresh_token or "")
        _write_env_kv("QBO_REALM_ID", str(realm_id))
        os.environ["QBO_REFRESH_TOKEN"] = auth_client.refresh_token or ""
        os.environ["QBO_REALM_ID"] = str(realm_id)
    except Exception as exc:
        logger.warning("[QBO] could not persist .env: %s", exc)

    # Reset the cached client so the next call sees the new tokens.
    try:
        from qbo.client import reset_qbo_client
        reset_qbo_client()
    except Exception:
        pass

    return {
        "success": True,
        "realm_id": str(realm_id),
        "environment": environment,
    }


def disconnect() -> dict[str, Any]:
    """Wipe local QBO tokens.

    Deletes ``qbo/.qbo_tokens.json``, clears ``QBO_REFRESH_TOKEN`` (and
    ``QBO_REALM_ID``) from ``.env``, and resets the cached QBO client.

    Returns ``{"success": True, "had_tokens": bool}``.
    """
    had_tokens = _TOKEN_CACHE.exists()
    if had_tokens:
        try:
            _TOKEN_CACHE.unlink()
        except Exception as exc:
            return {"success": False, "error": f"Could not delete token file: {exc}"}

    # Clear env keys.
    for key in ("QBO_REFRESH_TOKEN", "QBO_REALM_ID"):
        try:
            _write_env_kv(key, "")
        except Exception as exc:
            logger.warning("[QBO] could not clear %s in .env: %s", key, exc)
        os.environ.pop(key, None)

    try:
        from qbo.client import reset_qbo_client
        reset_qbo_client()
    except Exception:
        pass

    return {"success": True, "had_tokens": had_tokens}


def get_connection_status() -> dict[str, Any]:
    """Return a snapshot of the current QBO connection for the UI."""
    status: dict[str, Any] = {
        "has_tokens": _TOKEN_CACHE.exists(),
        "realm_id": "",
        "environment": os.environ.get("QBO_ENVIRONMENT", "sandbox"),
        "credentials_configured": bool(
            os.environ.get("QBO_CLIENT_ID")
            and os.environ.get("QBO_CLIENT_SECRET")
        ),
    }
    if status["has_tokens"]:
        try:
            data = json.loads(_TOKEN_CACHE.read_text(encoding="utf-8"))
            status["realm_id"] = str(data.get("realm_id") or "")
            if data.get("environment"):
                status["environment"] = str(data["environment"])
        except Exception:
            pass
    try:
        from qbo.config import is_mock_mode, is_write_enabled
        status["mock_mode"] = is_mock_mode()
        status["write_enabled"] = is_write_enabled()
    except Exception:
        status["mock_mode"] = True
        status["write_enabled"] = False
    return status


# ---------------------------------------------------------------------------
# .env helper
# ---------------------------------------------------------------------------

def _write_env_kv(key: str, value: str) -> None:
    """Upsert a single KEY=VALUE pair in ``.env``, creating the file if
    missing. Preserves order and comments. Quotes the value if it
    contains spaces or shell metacharacters."""
    if not _ENV_PATH.exists():
        _ENV_PATH.write_text(f"{key}={value}\n", encoding="utf-8")
        return

    lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    replaced = False
    safe_value = value
    if any(c in value for c in (" ", "#", "$", '"', "'")):
        safe_value = '"' + value.replace('"', '\\"') + '"'
    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        if "=" in stripped:
            k, _, _ = stripped.partition("=")
            if k.strip() == key:
                out.append(f"{key}={safe_value}")
                replaced = True
                continue
        out.append(line)
    if not replaced:
        out.append(f"{key}={safe_value}")
    _ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
