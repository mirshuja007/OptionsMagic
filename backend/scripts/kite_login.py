#!/usr/bin/env python3
"""Daily Kite Connect login: exchange a fresh request_token for today's
access_token and write it into .env.

Kite access tokens expire every day around 6 AM IST, so this needs to run
once per trading day before the backend starts (or the backend process
needs restarting after this runs, since app.data.kite_client caches the
client for the life of the process).

Run this from your OWN machine, not a shared or cloud environment — it's
your live broker session either way, and the automated mode below submits
your actual password.

--- Manual mode (default, recommended) ---
Completes the login in your browser; only ever needs KITE_API_KEY and
KITE_API_SECRET, never your password.

    cd backend
    export KITE_API_KEY=...
    export KITE_API_SECRET=...
    python scripts/kite_login.py

--- Automated mode (--auto, opt-in) ---
Submits your user ID, password, and a TOTP code (derived from your TOTP
secret via pyotp) directly to Zerodha's login endpoints, skipping the
browser step entirely. This uses undocumented endpoints that Kite Connect's
official docs do not describe or support — it's a widely-used pattern in
the personal-algo-trading community, not something Zerodha publishes an API
for, and it could stop working or draw extra scrutiny on your account at
any time. Use it only if you're comfortable with that tradeoff, and only
for your own account.

    cd backend
    export KITE_API_KEY=...
    export KITE_API_SECRET=...
    export KITE_USER_ID=...
    export KITE_PASSWORD=...
    export KITE_TOTP_SECRET=...   # the base32 TOTP seed, not a 6-digit code
    python scripts/kite_login.py --auto

Either mode writes KITE_API_KEY and KITE_ACCESS_TOKEN into backend/.env
(gitignored) and never prints or stores your password, TOTP secret, or API
secret anywhere.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from kiteconnect import KiteConnect
from kiteconnect.exceptions import KiteException

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def extract_request_token(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("http"):
        query = parse_qs(urlparse(raw).query)
        tokens = query.get("request_token")
        if not tokens:
            raise ValueError("No request_token query parameter found in that URL.")
        return tokens[0]
    return raw


def upsert_env_var(path: Path, key: str, value: str) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    pattern = re.compile(rf"^{re.escape(key)}=")
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")


def manual_request_token(kite: KiteConnect) -> str:
    print("1. Open this URL, log in with your Zerodha credentials + 2FA:\n")
    print(f"   {kite.login_url()}\n")
    print("2. After login you'll be redirected to your app's redirect URL with")
    print("   a `request_token` query parameter — paste that full URL (or just")
    print("   the token) below.\n")
    raw = input("Redirect URL or request_token: ")
    return extract_request_token(raw)


def automated_request_token(api_key: str, user_id: str, password: str, totp_secret: str) -> str:
    """Submits credentials + a live TOTP code directly to Zerodha's
    (undocumented) login endpoints instead of using a browser. See the
    module docstring's "Automated mode" section before using this.
    """
    import pyotp
    import requests

    session = requests.Session()

    login_resp = session.post(
        "https://kite.zerodha.com/api/login",
        data={"user_id": user_id, "password": password},
        timeout=10,
    )
    login_resp.raise_for_status()
    request_id = login_resp.json()["data"]["request_id"]

    totp_code = pyotp.TOTP(totp_secret).now()
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Skip the browser step and log in with KITE_USER_ID/KITE_PASSWORD/KITE_TOTP_SECRET (see module docstring).",
    )
    args = parser.parse_args()

    api_key = os.environ.get("KITE_API_KEY")
    api_secret = os.environ.get("KITE_API_SECRET")
    if not api_key or not api_secret:
        print("Set KITE_API_KEY and KITE_API_SECRET in your shell before running this script.", file=sys.stderr)
        return 1

    kite = KiteConnect(api_key=api_key)

    try:
        if args.auto:
            user_id = os.environ.get("KITE_USER_ID")
            password = os.environ.get("KITE_PASSWORD")
            totp_secret = os.environ.get("KITE_TOTP_SECRET")
            if not user_id or not password or not totp_secret:
                print(
                    "--auto requires KITE_USER_ID, KITE_PASSWORD, and KITE_TOTP_SECRET in your shell.",
                    file=sys.stderr,
                )
                return 1
            request_token = automated_request_token(api_key, user_id, password, totp_secret)
        else:
            request_token = manual_request_token(kite)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — surface any requests/HTTP failure with context, not a raw traceback
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1

    try:
        session_data = kite.generate_session(request_token, api_secret=api_secret)
    except KiteException as exc:
        print(f"Token exchange failed: {exc}", file=sys.stderr)
        return 1

    access_token = session_data["access_token"]
    upsert_env_var(ENV_PATH, "KITE_API_KEY", api_key)
    upsert_env_var(ENV_PATH, "KITE_ACCESS_TOKEN", access_token)

    # Verify the write actually stuck — immediately, and again after a short
    # delay. This machine has shown a reproducible pattern of .env reverting
    # to a stale token shortly after a successful write, with the cause
    # never pinned down across several rounds of manual checking (stale
    # terminal scrollback and a lingering Notepad window both turned out to
    # be red herrings, not the actual cause). Rather than rely on a human
    # re-checking at some later, unspecified moment, prove it here, now,
    # in one atomic script run.
    def _read_token() -> str | None:
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith("KITE_ACCESS_TOKEN="):
                return line.split("=", 1)[1]
        return None

    immediate = _read_token()
    if immediate != access_token:
        print(
            f"WARNING: wrote token ending ...{access_token[-6:]} but an immediate re-read shows "
            f"...{(immediate or '')[-6:]} instead — the write did not stick at all.",
            file=sys.stderr,
        )
        return 1

    time.sleep(3)
    delayed = _read_token()
    if delayed != access_token:
        print(
            f"WARNING: the token was correct immediately after writing, but {ENV_PATH} now reads "
            f"...{(delayed or '')[-6:]} just 3 seconds later — something else on this machine is "
            "modifying this file shortly after it's written. Check Task Scheduler, any 'protected "
            "folder' / ransomware-guard feature in installed security software, sync/backup tools "
            "watching this folder, or an editor with autosave pointed at this file.",
            file=sys.stderr,
        )
        return 1

    print(f"\nWrote KITE_API_KEY and KITE_ACCESS_TOKEN to {ENV_PATH}")
    print("Verified: the token is still correct 3 seconds after writing.")
    print("Restart the backend (or reload it) to pick up the new token.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
