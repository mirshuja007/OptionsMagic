"""Persistent log of real CAS session outcomes (reference price -> actual
settled close) — the only honest foundation for ever answering "what's the
probability of a CAS move like today's?" (see the module docstring context
in ``app.data.cas`` and this project's README for why: CAS only went live
2026-08-03, so there is no backtested model and no vendor-supplied
probability to lean on; a real answer has to come from accumulating actual
outcomes over time and reading off the empirical distribution).

One row per (session_date, symbol): the 3:00-3:15pm VWAP reference price,
the day's actual final close (captured only once the auction has genuinely
settled — see ``record_session_outcome``), and the %% move between them.
Logging is manual/on-demand (a CAS Monitor button), not scheduled — this
app has no background job runner, and a human clicking "log today" once
the auction is done is simpler and more honest than guessing when
"done" is.

Persistence caveat: this writes a plain CSV to disk
(``DEFAULT_LOG_PATH``), not a database. That's fine for a local run, but
**Streamlit Community Cloud's filesystem is not durable** — it resets on
every redeploy and on every sleep/wake cycle for an idle app. A log
building up during a live Cloud session can vanish the next time the app
restarts. For the history to actually accumulate over weeks/months on
Cloud, the CSV needs to be committed back to the git repo periodically
(e.g. download it via the CAS Monitor's export button and commit it, or
ask for it to be committed the next time this project is worked on) —
there is no automated commit-back wired up here.
"""
from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.data.cas import MAGNITUDE_BUCKETS, IST, cas_window_status, magnitude_bucket, reference_price

# backend/app/data/cas_history.py -> repo root is 3 parents up.
DEFAULT_LOG_PATH = Path(__file__).resolve().parents[3] / "data" / "cas_session_log.csv"

_FIELDS = ["session_date", "symbol", "reference_price", "final_price", "move_pct", "captured_at"]

# CAS phases in which "the day's last bar" is actually the genuine settled
# close, not just wherever continuous trading happened to be mid-session.
_SETTLED_PHASES = {"transition", "post_close", "closed"}


@dataclass(frozen=True)
class CASSessionRecord:
    session_date: date
    symbol: str
    reference_price: float
    final_price: float
    move_pct: float  # (final - reference) / reference * 100
    captured_at: datetime  # IST wall-clock, naive — when this row was logged


def _read_all(path: Path) -> list[CASSessionRecord]:
    if not path.exists():
        return []
    records = []
    with path.open("r", newline="") as f:
        for row in csv.DictReader(f):
            records.append(
                CASSessionRecord(
                    session_date=date.fromisoformat(row["session_date"]),
                    symbol=row["symbol"],
                    reference_price=float(row["reference_price"]),
                    final_price=float(row["final_price"]),
                    move_pct=float(row["move_pct"]),
                    captured_at=datetime.fromisoformat(row["captured_at"]),
                )
            )
    return records


def _write_all(records: list[CASSessionRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_FIELDS)
        for r in records:
            writer.writerow(
                [r.session_date.isoformat(), r.symbol, r.reference_price, r.final_price, r.move_pct,
                 r.captured_at.isoformat()]
            )


def load_records(symbol: str | None = None, path: Path = DEFAULT_LOG_PATH) -> list[CASSessionRecord]:
    """All logged records, optionally filtered to one symbol, oldest first."""
    records = _read_all(path)
    if symbol is not None:
        records = [r for r in records if r.symbol == symbol]
    return sorted(records, key=lambda r: r.session_date)


def record_session_outcome(
    symbol: str,
    session_date: date | None = None,
    *,
    now: datetime | None = None,
    path: Path = DEFAULT_LOG_PATH,
) -> CASSessionRecord:
    """Capture ``symbol``'s CAS outcome for ``session_date`` (default today)
    from its minute-bar series: the 3:00-3:15pm VWAP reference price vs. the
    day's actual final close (the series' last bar).

    Raises ``ValueError`` — rather than silently recording a mid-session
    price as if it were the settled close — when:
    - ``session_date`` is in the future;
    - ``session_date`` is today and the CAS auction hasn't actually settled
      yet (before 3:35pm IST): there's no true "final price" yet, only
      wherever continuous/reference-window trading happened to be;
    - the series doesn't cover the 3:00-3:15pm reference window at all.

    Replaces (not duplicates) any existing record for the same
    (session_date, symbol) — logging twice in a day overwrites, it
    doesn't skew the history with a repeated row.
    """
    from app.data.feed import generate_minute_series  # local import: feed does not import cas_history

    session_date = session_date or date.today()
    today = date.today()
    if session_date > today:
        raise ValueError(f"can't log a future session date ({session_date})")
    if session_date == today:
        status = cas_window_status(now)
        if status.phase not in _SETTLED_PHASES:
            raise ValueError(
                f"today's CAS auction hasn't settled yet ({status.label}); "
                "log after 3:35pm IST once there's a real final price."
            )

    series = generate_minute_series(symbol, session_date=session_date)
    if not series:
        raise ValueError(f"no data returned for {symbol} on {session_date}")

    ref = reference_price(series)
    if ref is None:
        raise ValueError(f"{symbol}'s series on {session_date} doesn't cover the 3:00-3:15pm reference window")

    final_price = series[-1][1]
    move_pct = (final_price - ref) / ref * 100.0
    record = CASSessionRecord(
        session_date=session_date,
        symbol=symbol,
        reference_price=ref,
        final_price=final_price,
        move_pct=round(move_pct, 4),
        captured_at=datetime.now(IST).replace(tzinfo=None),
    )

    existing = [r for r in _read_all(path) if not (r.session_date == session_date and r.symbol == symbol)]
    existing.append(record)
    _write_all(existing, path)
    return record


@dataclass(frozen=True)
class CASHistorySummary:
    """Real, purely empirical stats over whatever's actually been logged so
    far for one symbol — NOT a fitted/backtested model, NOT extrapolated.
    ``n_sessions`` is the whole point: read every other field in light of
    it. A handful of sessions is not a reliable distribution; treat this as
    "what's happened so far," not "what will happen."
    """

    symbol: str
    n_sessions: int
    mean_move_pct: float
    std_move_pct: float | None  # None when n_sessions < 2 (stdev undefined)
    min_move_pct: float
    max_move_pct: float
    bucket_counts: dict[str, int]  # magnitude-bucket label -> count (direction-agnostic)
    pct_upside: float
    pct_downside: float
    pct_flat: float


def summarize_history(
    symbol: str, flat_threshold_pct: float = 0.02, path: Path = DEFAULT_LOG_PATH
) -> CASHistorySummary | None:
    """Empirical move-size distribution from every logged session for
    ``symbol`` — ``None`` if nothing's been logged yet. See
    ``CASHistorySummary``'s docstring: this reports what happened, not a
    prediction, and always carries ``n_sessions`` so thin samples are
    self-evidently thin.
    """
    records = load_records(symbol, path)
    if not records:
        return None

    moves = [r.move_pct for r in records]
    n = len(moves)
    mean = statistics.fmean(moves)
    std = statistics.stdev(moves) if n >= 2 else None

    buckets = {label: 0 for _, label in MAGNITUDE_BUCKETS}
    for m in moves:
        buckets[magnitude_bucket(abs(m))] += 1

    n_up = sum(1 for m in moves if m > flat_threshold_pct)
    n_down = sum(1 for m in moves if m < -flat_threshold_pct)
    n_flat = n - n_up - n_down

    return CASHistorySummary(
        symbol=symbol,
        n_sessions=n,
        mean_move_pct=round(mean, 3),
        std_move_pct=round(std, 3) if std is not None else None,
        min_move_pct=round(min(moves), 3),
        max_move_pct=round(max(moves), 3),
        bucket_counts=buckets,
        pct_upside=round(n_up / n * 100.0, 1),
        pct_downside=round(n_down / n * 100.0, 1),
        pct_flat=round(n_flat / n * 100.0, 1),
    )
