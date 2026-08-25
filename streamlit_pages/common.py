"""Shared plumbing for the Streamlit frontend: making the backend's ``app.*``
package importable, syncing Streamlit Cloud Secrets into the env vars the
backend actually reads, and small formatting/error helpers mirroring the
Next.js frontend's conventions (lib/api.ts's error surfacing, the
fmt/fmtCurrency helpers scattered across the React components).

This app deliberately imports backend Python modules directly rather than
calling the FastAPI backend over HTTP — Streamlit Cloud hosts exactly one
process, so there's no separate reachable backend service to call. Same
analytics/solver/feed code as the Next.js app, different UI layer.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_SECRETS_CANDIDATES = [
    Path.home() / ".streamlit" / "secrets.toml",
    Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml",
]


def _secrets_file_exists() -> bool:
    """Streamlit renders a "No secrets found" warning banner into the app
    the moment ``st.secrets`` is accessed with no secrets.toml present,
    regardless of try/except around it — there's no exception to catch
    that suppresses it, only avoiding the access in the first place.
    """
    return any(p.exists() for p in _SECRETS_CANDIDATES)


def get_secret(key: str, default: str | None = None) -> str | None:
    """Reads one key from Streamlit Secrets, or ``default`` if there's no
    secrets.toml or the key isn't set there. Safe to call even when no
    secrets.toml exists (see ``_secrets_file_exists``).
    """
    if not _secrets_file_exists():
        return default

    import streamlit as st

    try:
        value = st.secrets[key]
    except KeyError:
        return default
    return str(value) if value else default


def sync_secrets_to_env() -> None:
    """Streamlit Cloud's Secrets panel populates ``st.secrets``, not
    ``os.environ`` — the backend modules (kite_client.py, feed.py) only
    ever read plain env vars, so copy the few they care about across once
    at startup rather than touching backend code to special-case Streamlit.
    Running locally without a secrets.toml is a no-op here; a real ``.env``
    picked up some other way (e.g. already exported) still works normally.
    """
    for key in ("MARKET_DATA_PROVIDER", "KITE_API_KEY", "KITE_ACCESS_TOKEN"):
        value = get_secret(key)
        if value:
            os.environ[key] = value


def safe_call(fn, *args, **kwargs):
    """Call ``fn``, returning ``(result, error_message)``. Mirrors the
    FastAPI routes' exception handling (KeyError -> unknown symbol,
    KiteAuthError -> needs a fresh token, KiteFeedError -> feed/network
    issue) without needing HTTP status codes to carry the distinction.
    """
    from app.data.kite_client import KiteAuthError
    from app.data.kite_feed import KiteFeedError

    try:
        return fn(*args, **kwargs), None
    except KeyError as exc:
        return None, f"Unknown symbol: {exc}"
    except KiteAuthError as exc:
        return None, f"Kite auth error: {exc}"
    except KiteFeedError as exc:
        return None, f"Feed error: {exc}"


def fmt(n: float, digits: int = 2) -> str:
    return f"{n:,.{digits}f}"


def fmt_currency(n: float) -> str:
    return f"₹{n:,.0f}"


def strategy_label(strategy_type: str) -> str:
    return " ".join(w.capitalize() for w in strategy_type.split("_"))
