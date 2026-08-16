"""Authenticated Kite Connect client, built once per process from env vars.

Required env vars:
  KITE_API_KEY       API key from the Kite Connect developer console.
  KITE_ACCESS_TOKEN  Today's access token. Kite access tokens expire daily
                      around 6 AM IST — refresh this every trading day via
                      ``scripts/kite_login.py`` before the process starts (or
                      restart the process after refreshing it; this module
                      caches the client for the life of the process).

KITE_API_SECRET is deliberately not read here — it's only needed for the
one-time login exchange (request_token -> access_token) in
``scripts/kite_login.py``, not for day-to-day API calls, so it never needs
to be available to the running server process.
"""
from __future__ import annotations

import os

from kiteconnect import KiteConnect


class KiteAuthError(RuntimeError):
    """Raised when Kite credentials are missing or rejected."""


_client: KiteConnect | None = None


def get_kite_client() -> KiteConnect:
    """Returns a process-wide cached, authenticated KiteConnect instance."""
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("KITE_API_KEY")
    access_token = os.environ.get("KITE_ACCESS_TOKEN")
    if not api_key or not access_token:
        raise KiteAuthError(
            "KITE_API_KEY and KITE_ACCESS_TOKEN must be set. Run "
            "scripts/kite_login.py to obtain today's access token — Kite "
            "tokens expire daily, so this is a once-per-trading-day step."
        )

    client = KiteConnect(api_key=api_key)
    client.set_access_token(access_token)
    _client = client
    return _client


def reset_kite_client() -> None:
    """Drops the cached client, forcing the next call to re-read env vars.
    Mainly useful for tests and for picking up a freshly refreshed token
    without restarting the process.
    """
    global _client
    _client = None
