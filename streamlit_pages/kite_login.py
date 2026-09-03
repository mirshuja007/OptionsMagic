"""In-app "Login with Kite" panel for Research Mode.

Kite access tokens expire daily (~6 AM IST), and on Streamlit Cloud the
usual fix — rerunning ``backend/scripts/kite_login.py`` locally and pasting
the new token into the app's Secrets — means a manual round trip outside
the app every trading day. This panel does that same exchange from inside
Research Mode instead: you click a Kite login link, log in in your own
browser, and paste back the redirect URL/request_token Kite hands you.
Only ``request_token`` — a short-lived, single-use code — ever reaches
this app; your password never does. The actual token exchange
(``KiteConnect.generate_session``) calls Kite Connect's official,
documented API endpoint.

An earlier version of this panel also offered an "Auto login" mode
(User ID/Password/TOTP submitted directly, no browser round trip, via
``app.data.kite_auth.automated_request_token``) — removed because it calls
Zerodha's *undocumented* login endpoints, which were consistently observed
to reject requests from Streamlit Cloud with a 403 regardless of
credentials: it never actually worked on this app's real deployment, only
added a dead-end option. That automated-login code path still exists and
still works for local, non-Cloud use in ``backend/scripts/kite_login.py
--auto`` (run from your own machine, not blocked by any Cloud IP) — this
in-app panel just no longer offers it, since Streamlit Cloud is the
context it actually runs in.

A successful login is cached to a small local file, dated to today (IST)
— the next time this page loads with no active session but that same-day
cache still present (e.g. after a script rerun or a fresh browser tab
reconnecting to the same running app), a "Use cached session" button
appears so you don't have to repeat the login. This is purely a
convenience shortcut on top of the in-memory session below, not a
persistence guarantee: Streamlit Cloud doesn't promise the local
filesystem survives a container restart (redeploys and idle sleep/wake
cycles both reset it), so a Cloud reboot may still need a fresh login
regardless — and Kite tokens expire once every 24 hours no matter what,
so a new trading day always needs a fresh login too.

Security notes:

- A successful login updates *this running app process's* in-memory
  environment (via ``os.environ`` + ``reset_kite_client()``) and the
  same-day disk cache described above — neither can rewrite Streamlit
  Cloud's platform Secrets.
- The disk cache holds only the access_token (not your password or API
  secret), scoped to today's date and the current API key — see
  ``_load_cached_session``. It lives at
  ``.streamlit/kite_session_cache.json``, which is gitignored.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

import streamlit as st

from streamlit_pages.common import get_secret

_CACHE_PATH = Path(__file__).resolve().parent.parent / ".streamlit" / "kite_session_cache.json"


def _today_ist() -> date:
    from app.core.timezone import IST

    return datetime.now(IST).date()


def _load_cached_session(api_key: str) -> str | None:
    """Today's cached access_token for this api_key, if one was saved
    earlier in the day — silently returns ``None`` on any missing,
    malformed, stale (not today), or api_key-mismatched cache rather than
    raising, since this is purely a convenience shortcut, not the source
    of truth for whether a session is valid.
    """
    try:
        data = json.loads(_CACHE_PATH.read_text())
    except (OSError, ValueError):
        return None
    if data.get("api_key") != api_key or data.get("date") != _today_ist().isoformat():
        return None
    return data.get("access_token") or None


def _save_cached_session(api_key: str, access_token: str) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps({"api_key": api_key, "access_token": access_token, "date": _today_ist().isoformat()})
        )
    except OSError:
        pass  # best-effort convenience cache — never block a successful login on this


def _finish_login(api_key: str, access_token: str) -> None:
    """Common tail of a successful login: activate the session for this
    running process, cache it for next time, and rerun to show it live.
    """
    from app.data.kite_client import reset_kite_client

    os.environ["KITE_API_KEY"] = api_key
    os.environ["KITE_ACCESS_TOKEN"] = access_token
    reset_kite_client()
    _save_cached_session(api_key, access_token)
    st.success("Logged in — today's Kite session is active.")
    st.rerun()


def render_kite_login_panel() -> None:
    with st.expander("🔑 Login with Kite (refresh today's session)"):
        api_key = get_secret("KITE_API_KEY") or os.environ.get("KITE_API_KEY", "")
        api_secret = get_secret("KITE_API_SECRET") or os.environ.get("KITE_API_SECRET", "")

        if not api_key or not api_secret:
            st.warning(
                "Set KITE_API_KEY and KITE_API_SECRET in this app's Secrets first "
                "(App settings → Secrets)."
            )
            return

        cached_token = _load_cached_session(api_key)
        if cached_token and os.environ.get("KITE_ACCESS_TOKEN") != cached_token:
            st.info("A cached session token from earlier today was found.")
            if st.button("Use cached session"):
                _finish_login(api_key, cached_token)

        _render_paste_token(api_key, api_secret)


def _render_paste_token(api_key: str, api_secret: str) -> None:
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=api_key)
    st.caption(
        "Exchanges a fresh Kite login for today's access token. Your password never reaches this app — "
        "only the short-lived request_token Kite hands back after you log in yourself, below."
    )
    st.markdown(f"**1.** [Log in with Zerodha]({kite.login_url()}) (opens in a new tab)")
    st.caption("**2.** After logging in, you'll land on a redirect URL containing `request_token=...` — paste that full URL (or just the token) below.")

    with st.form("kite_paste_token_form", clear_on_submit=True):
        raw = st.text_input("Redirect URL or request_token")
        submitted = st.form_submit_button("Exchange token")

    if not submitted:
        return
    if not raw:
        st.error("Paste the redirect URL or request_token first.")
        return

    with st.spinner("Exchanging token..."):
        try:
            from kiteconnect.exceptions import KiteException

            from app.data.kite_auth import extract_request_token

            request_token = extract_request_token(raw)
            session_data = kite.generate_session(request_token, api_secret=api_secret)
        except ValueError as exc:
            st.error(str(exc))
            return
        except KiteException as exc:
            st.error(f"Token exchange failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 — surface any other failure with context
            st.error(f"Token exchange failed: {exc}")
            return

    _finish_login(api_key, session_data["access_token"])
