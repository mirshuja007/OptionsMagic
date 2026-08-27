"""In-app "Login with Kite" panel for Research Mode.

Kite access tokens expire daily (~6 AM IST), and on Streamlit Cloud the
usual fix — rerunning ``backend/scripts/kite_login.py`` locally and pasting
the new token into the app's Secrets — means a manual round trip outside
the app every trading day. This panel does that same exchange from inside
Research Mode instead, two ways:

- **Paste token** (default, recommended): you click a Kite login link,
  log in in your own browser, and paste back the redirect URL/request_token
  Kite hands you. Only ``request_token`` — a short-lived, single-use code —
  ever reaches this app; your password never does. The actual token
  exchange (``KiteConnect.generate_session``) calls Kite Connect's
  official, documented API endpoint, not the undocumented web-login
  endpoints — see the "Known limitation" note below for why that
  distinction matters on Streamlit Cloud specifically.
- **Auto login** (User ID/Password/TOTP): does the whole thing without a
  browser round trip, via ``app.data.kite_auth.automated_request_token``.
  Convenient, but calls Zerodha's *undocumented* login endpoints, which
  have been observed to reject requests from Streamlit Cloud outright —
  see below. Prefer "Paste token" on Cloud; this mode is more likely to
  work when running the app locally.

A successful login (either mode) is cached to a small local file, dated to
today (IST) — the next time this page loads with no active session but
that same-day cache still present (e.g. after a script rerun or a fresh
browser tab reconnecting to the same running app), a "Use cached session"
button appears so you don't have to repeat the login. This is purely a
convenience shortcut on top of the in-memory session below, not a
persistence guarantee: Streamlit Cloud doesn't promise the local
filesystem survives a container restart, so a Cloud reboot may still need
a fresh login regardless.

Security notes:

- In Auto login mode, your Zerodha password is only ever held in that
  form's own input value for the duration of one click — it is never
  written to Streamlit Secrets, session_state that survives a rerun, disk,
  or logs. Every login submits it fresh. Paste token mode never asks for
  it at all.
- ``KITE_TOTP_SECRET`` (your TOTP seed, not a 6-digit code) is optional in
  Secrets, used only by Auto login. If set, the current code is generated
  automatically each login; if not, you enter the 6-digit code by hand.
- A successful login updates *this running app process's* in-memory
  environment (via ``os.environ`` + ``reset_kite_client()``) and the
  same-day disk cache described above — neither can rewrite Streamlit
  Cloud's platform Secrets.
- The disk cache holds only the access_token (not your password, TOTP
  secret, or API secret), scoped to today's date and the current API key —
  see ``_load_cached_session``. It lives at ``.streamlit/kite_session_cache.json``,
  which is gitignored.

Known limitation (Auto login only): on Streamlit Community Cloud, Auto
login has been observed to fail with "403 Forbidden" on Zerodha's
``/api/twofa`` endpoint even with correct credentials and browser-like
request headers — most likely Zerodha rejecting the request based on
Streamlit Cloud's datacenter IP address, not anything about the request
itself. There's no header or retry fix for that from this side. Paste
token mode sidesteps it entirely, since the browser login happens on
*your* device, not Streamlit Cloud's.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

import requests
import streamlit as st

from streamlit_pages.common import get_secret

_CACHE_PATH = Path(__file__).resolve().parent.parent / ".streamlit" / "kite_session_cache.json"


def _today_ist() -> date:
    from app.data.cas import IST

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

        mode = st.radio(
            "Login method",
            ["Paste token", "Auto login"],
            horizontal=True,
            help="Paste token: log in yourself in a browser, paste back the result — works from Streamlit "
            "Cloud. Auto login: submits User ID/Password/TOTP for you, but Zerodha has been observed to "
            "block this from Streamlit Cloud's IP (see the panel below once selected).",
        )
        if mode == "Paste token":
            _render_paste_token(api_key, api_secret)
        else:
            _render_auto_login(api_key, api_secret)


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


def _render_auto_login(api_key: str, api_secret: str) -> None:
    st.caption(
        "Submits your Zerodha login for you — convenient, but see the module docstring's \"Known "
        "limitation\": this has been observed to 403 from Streamlit Cloud regardless of credentials. "
        "Prefer \"Paste token\" above if you hit that."
    )
    totp_secret = get_secret("KITE_TOTP_SECRET")

    with st.form("kite_auto_login_form", clear_on_submit=True):
        user_id = st.text_input("Zerodha User ID")
        password = st.text_input("Zerodha Password", type="password")
        if totp_secret:
            st.caption("TOTP code will be generated automatically from KITE_TOTP_SECRET in Secrets.")
            totp_code = None
        else:
            totp_code = st.text_input("TOTP code (6 digits, from your authenticator app)")
        submitted = st.form_submit_button("Log in")

    if not submitted:
        return

    if not user_id or not password or (not totp_secret and not totp_code):
        st.error("Fill in all fields before submitting.")
        return

    with st.spinner("Logging in to Kite..."):
        try:
            from kiteconnect import KiteConnect
            from kiteconnect.exceptions import KiteException

            from app.data.kite_auth import automated_request_token, request_token_with_totp_code

            if totp_secret:
                request_token = automated_request_token(api_key, user_id, password, totp_secret)
            else:
                request_token = request_token_with_totp_code(api_key, user_id, password, totp_code)

            kite = KiteConnect(api_key=api_key)
            session_data = kite.generate_session(request_token, api_secret=api_secret)
        except KiteException as exc:
            st.error(f"Token exchange failed: {exc}")
            return
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 403:
                st.error(
                    "Login failed: 403 Forbidden from Zerodha. This usually means Zerodha is blocking "
                    "the request based on Streamlit Cloud's IP address, not your credentials — try "
                    "\"Paste token\" above instead."
                )
            else:
                st.error(f"Login failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 — surface any other login/HTTP failure with context
            st.error(f"Login failed: {exc}")
            return
        finally:
            # Best-effort scrub — Python can't guarantee immediate GC/zeroing,
            # but there's no reason to keep a reference around longer than this.
            password = None  # noqa: F841

    _finish_login(api_key, session_data["access_token"])
