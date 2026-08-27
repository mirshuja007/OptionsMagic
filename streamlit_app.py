"""Custom-Mojo OptionsMagic — Streamlit frontend.

A Streamlit port of the Next.js Research Mode / Strategy Command Mode UI,
built so this project can deploy to Streamlit Community Cloud, which hosts
exactly one Python script and can't run a separate FastAPI backend process
or a Next.js build alongside it. Rather than calling the FastAPI backend
over HTTP, this app imports the backend's Python modules directly
(app.data.feed, app.analytics.*, app.strategy.*) and runs entirely
in-process — same analytics/solver/margin code as the Next.js app, a
different UI layer on top.

Kite Connect access tokens expire daily (~6 AM IST) and refreshing them
requires running backend/scripts/kite_login.py, which needs a real Kite
login (and, if automated, your TOTP secret) — that doesn't happen on its
own on a hosted platform. On a public Streamlit Cloud deployment, default
to MARKET_DATA_PROVIDER=mock unless you specifically want live data and
are prepared to re-paste a fresh KITE_ACCESS_TOKEN into Secrets every
trading morning.
"""
from __future__ import annotations

import streamlit as st

from streamlit_pages.common import sync_secrets_to_env

st.set_page_config(page_title="Custom-Mojo | Options Suite", page_icon="📈", layout="wide")
sync_secrets_to_env()

from app.data.feed import get_active_provider  # noqa: E402 (must follow sys.path/secrets setup above)

st.markdown(
    """
    <style>
    div[data-testid="stMetricValue"] { font-family: 'DejaVu Sans Mono', monospace; }
    div[data-testid="stMetric"] {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 8px;
        padding: 10px 14px 4px 14px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

try:
    provider = get_active_provider()
except RuntimeError as exc:
    st.error(str(exc))
    st.stop()

badge = "🟢 Live (Kite)" if provider == "kite" else "🟠 Simulated Data"
st.sidebar.title("Custom-Mojo")
st.sidebar.caption(f"Options Suite · {badge}")
if provider != "kite":
    st.sidebar.caption("Set MARKET_DATA_PROVIDER=kite (+ KITE_API_KEY/KITE_ACCESS_TOKEN) in Secrets for live data.")

page = st.sidebar.radio(
    "Navigate", ["Research Mode", "Strategy Command Mode", "CAS Monitor"], label_visibility="collapsed"
)

if page == "Research Mode":
    from streamlit_pages.research import render as render_research

    render_research()
elif page == "Strategy Command Mode":
    from streamlit_pages.strategy import render as render_strategy

    render_strategy()
else:
    from streamlit_pages.cas import render as render_cas

    render_cas()
