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

Security notes:

- In Auto login mode, your Zerodha password is only ever held in that
  form's own input value for the duration of one click — it is never
  written to Streamlit Secrets, session_state that survives a rerun, disk,
  or logs. Every login submits it fresh. Paste token mode never asks for
  it at all.
- ``KITE_TOTP_SECRET`` (your TOTP seed, not a 6-digit code) is optional in
  Secrets, used only by Auto login. If set, the current code is generated
  automatically each login; if not, you enter the 6-digit code by hand.
- A successful login here only updates *this running app process's*
  in-memory environment (via ``os.environ`` + ``reset_kite_client()``) —
  it cannot rewrite Streamlit Cloud's platform Secrets. A Cloud reboot or
  redeploy still needs a fresh login through this panel (or the Secrets
  paste, or the local script) again.

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

import os

import requests
import streamlit as st

from streamlit_pages.common import get_secret


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
            from app.data.kite_client import reset_kite_client

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

    os.environ["KITE_API_KEY"] = api_key
    os.environ["KITE_ACCESS_TOKEN"] = session_data["access_token"]
    reset_kite_client()
    st.success("Logged in — today's Kite session is active.")
    st.rerun()


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
            from app.data.kite_client import reset_kite_client

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

    os.environ["KITE_API_KEY"] = api_key
    os.environ["KITE_ACCESS_TOKEN"] = session_data["access_token"]
    reset_kite_client()
    st.success("Logged in — today's Kite session is active.")
    st.rerun()
