"""In-app "Login with Kite" panel for Research Mode.

Kite access tokens expire daily (~6 AM IST), and on Streamlit Cloud the
usual fix — rerunning ``backend/scripts/kite_login.py`` locally and pasting
the new token into the app's Secrets — means a manual round trip outside
the app every trading day. This panel does the same login (via
``app.data.kite_auth.automated_request_token``, the same code the local
script uses) from inside Research Mode instead.

Security notes, since this trades off differently than the local script:

- Your Zerodha password is only ever held in this widget's own input value
  for the duration of one click — it is never written to Streamlit Secrets,
  session_state that survives a rerun, disk, or logs. Every login submits
  it fresh.
- ``KITE_TOTP_SECRET`` (your TOTP seed, not a 6-digit code) is optional in
  Secrets. If set, the current code is generated automatically each login.
  If not set, you enter the 6-digit code from your authenticator app by
  hand each time — slower, but means the seed itself is never stored here.
- A successful login here only updates *this running app process's*
  in-memory environment (via ``os.environ`` + ``reset_kite_client()``) —
  it cannot rewrite Streamlit Cloud's platform Secrets. A Cloud reboot or
  redeploy still needs a fresh login through this panel (or the Secrets
  paste, or the local script) again.
- ``automated_request_token`` submits your credentials to Zerodha endpoints
  that Kite Connect's official docs don't describe or support — see its
  docstring in ``backend/app/data/kite_auth.py`` for the same tradeoff the
  local script's docstring already documents.
"""
from __future__ import annotations

import os

import streamlit as st

from streamlit_pages.common import get_secret


def render_kite_login_panel() -> None:
    with st.expander("🔑 Login with Kite (refresh today's session)"):
        st.caption(
            "Exchanges a fresh Kite login for today's access token, without leaving this "
            "app. Your password is never stored — it's used once for this login and discarded."
        )

        api_key = get_secret("KITE_API_KEY") or os.environ.get("KITE_API_KEY", "")
        api_secret = get_secret("KITE_API_SECRET") or os.environ.get("KITE_API_SECRET", "")
        totp_secret = get_secret("KITE_TOTP_SECRET")

        if not api_key or not api_secret:
            st.warning(
                "Set KITE_API_KEY and KITE_API_SECRET in this app's Secrets first "
                "(App settings → Secrets)."
            )
            return

        with st.form("kite_login_form", clear_on_submit=True):
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
            except Exception as exc:  # noqa: BLE001 — surface any login/HTTP failure with context
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
