#!/usr/bin/env python3
"""Daily Kite Connect login: exchange a fresh request_token for today's
access_token and write it into .env.

Kite access tokens expire every day around 6 AM IST, so this needs to run
once per trading day before the backend starts (or the backend process
needs restarting after this runs, since app.data.kite_client caches the
client for the life of the process).

Usage:
    cd backend
    export KITE_API_KEY=...      # from the Kite Connect developer console
    export KITE_API_SECRET=...   # never put this in a committed file
    python scripts/kite_login.py

The script prints a login URL, you complete the login in a browser, and
paste back either the full redirect URL or just the `request_token` query
parameter from it. On success it writes/updates KITE_ACCESS_TOKEN in
backend/.env (creating the file if needed) and never prints or stores
KITE_API_SECRET anywhere.
"""
from __future__ import annotations

import os
import re
import sys
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


def main() -> int:
    api_key = os.environ.get("KITE_API_KEY")
    api_secret = os.environ.get("KITE_API_SECRET")
    if not api_key or not api_secret:
        print("Set KITE_API_KEY and KITE_API_SECRET in your shell before running this script.", file=sys.stderr)
        return 1

    kite = KiteConnect(api_key=api_key)
    print("1. Open this URL, log in with your Zerodha credentials + 2FA:\n")
    print(f"   {kite.login_url()}\n")
    print("2. After login you'll be redirected to your app's redirect URL with")
    print("   a `request_token` query parameter — paste that full URL (or just")
    print("   the token) below.\n")

    raw = input("Redirect URL or request_token: ")
    try:
        request_token = extract_request_token(raw)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        session = kite.generate_session(request_token, api_secret=api_secret)
    except KiteException as exc:
        print(f"Login exchange failed: {exc}", file=sys.stderr)
        return 1

    access_token = session["access_token"]
    upsert_env_var(ENV_PATH, "KITE_API_KEY", api_key)
    upsert_env_var(ENV_PATH, "KITE_ACCESS_TOKEN", access_token)
    print(f"\nWrote KITE_API_KEY and KITE_ACCESS_TOKEN to {ENV_PATH}")
    print("Restart the backend (or reload it) to pick up the new token.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
