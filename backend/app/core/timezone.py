"""Shared IST (India Standard Time) timezone constant.

Used anywhere "now" needs to be read as IST wall-clock time rather than
the host machine's own system clock — critical on cloud deployments
(Streamlit Cloud, most CI) where the container's system timezone is UTC,
not IST. A bare ``datetime.now()`` there silently misreads "now" by the
full UTC-IST offset when compared against IST-intended values (NSE
session start/end times, etc.). Fix pattern at every call site:
``datetime.now(IST).replace(tzinfo=None)`` — a naive datetime that
correctly represents IST wall-clock "now", directly comparable against
this codebase's other IST-intended naive datetimes.
"""
from __future__ import annotations

from datetime import timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
