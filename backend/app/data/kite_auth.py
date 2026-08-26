"""Request-token acquisition for Kite Connect logins — shared by
``scripts/kite_login.py`` (run from your own machine) and the in-app
"Login with Kite" panel in Research Mode.

Kept separate from kite_client.py because that module only ever handles an
*already-issued* access token; everything here is about obtaining a fresh
request_token, either via the normal browser redirect (``extract_request_token``)
or via Zerodha's undocumented direct-login endpoints (``automated_request_token``,
which needs your account password and a live TOTP code — see the callers'
own docstrings for the security tradeoffs before using it).
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

# Zerodha's login endpoints are undocumented and appear to reject requests
# that don't look like they came from a browser (kite.zerodha.com itself) —
# a bare requests.Session with no User-Agent/Referer/Origin has been
# observed to get a 403 from /api/twofa even with correct credentials.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://kite.zerodha.com/",
    "Origin": "https://kite.zerodha.com",
    "X-Kite-Version": "3.0.0",
}


def extract_request_token(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("http"):
        query = parse_qs(urlparse(raw).query)
        tokens = query.get("request_token")
        if not tokens:
            raise ValueError("No request_token query parameter found in that URL.")
        return tokens[0]
    return raw


def request_token_with_totp_code(api_key: str, user_id: str, password: str, totp_code: str) -> str:
    """Submits credentials + an already-generated 6-digit TOTP code directly
    to Zerodha's (undocumented) login endpoints instead of using a browser.
    This uses endpoints Kite Connect's official docs do not describe or
    support, and submits your actual account password — see the callers'
    docstrings before using it.
    """
    import requests

    session = requests.Session()
    session.headers.update(_BROWSER_HEADERS)

    login_resp = session.post(
        "https://kite.zerodha.com/api/login",
        data={"user_id": user_id, "password": password},
        timeout=10,
    )
    login_resp.raise_for_status()
    request_id = login_resp.json()["data"]["request_id"]

    twofa_resp = session.post(
        "https://kite.zerodha.com/api/twofa",
        data={"user_id": user_id, "request_id": request_id, "twofa_value": totp_code, "twofa_type": "totp"},
        timeout=10,
    )
    twofa_resp.raise_for_status()

    connect_url = f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"
    for _ in range(5):
        resp = session.get(connect_url, allow_redirects=False, timeout=10)
        if resp.status_code not in (301, 302, 303, 307, 308):
            raise RuntimeError(f"Expected a redirect from Kite, got HTTP {resp.status_code} instead.")
        location = resp.headers.get("Location", "")
        if "request_token=" in location:
            return extract_request_token(location)
        connect_url = location

    raise RuntimeError("Did not find request_token after following redirects — Kite's login flow may have changed.")


def automated_request_token(api_key: str, user_id: str, password: str, totp_secret: str) -> str:
    """Same as ``request_token_with_totp_code``, but derives the TOTP code
    from a stored TOTP seed (``totp_secret``) instead of taking an
    already-generated code.
    """
    import pyotp

    totp_code = pyotp.TOTP(totp_secret).now()
    return request_token_with_totp_code(api_key, user_id, password, totp_code)
