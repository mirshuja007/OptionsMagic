"""Shared Streamlit UI for assembling an ``app.analytics.conditions`` tree
out of dynamic rows — one "Add condition" row-editor, reused by Backtest
Lab's "Custom (Condition Builder)" strategy and its live counterpart so
the row add/remove/combine widgets aren't duplicated across two pages.

Deliberately flat, not a nested AND/OR tree builder: one list of leaf
conditions per side (entry/exit), combined by a single AND/OR choice —
this matches what was actually asked for ("i will use and/or logic"),
not a general expression-tree editor nobody asked for.
"""
from __future__ import annotations

import streamlit as st

from app.analytics.conditions import (
    AlwaysFalse,
    AlwaysTrue,
    And,
    Condition,
    GapCondition,
    GreekCondition,
    IvRegimeCondition,
    Or,
    PremiumChangeCondition,
    StraddlePctCondition,
    VixCondition,
)

OPERATORS = [">", ">=", "<", "<=", "==", "!="]
CONDITION_TYPES = ["Gap", "VIX", "ATM straddle %", "Option premium change", "Greek", "IV regime"]

__all__ = ["render_condition_set"]


def _row_condition(key_prefix: str, row_id: int, condition_type: str, premium_references: list[str]) -> Condition:
    k = f"{key_prefix}_{row_id}"
    if condition_type == "Gap":
        c1, c2, c3 = st.columns(3)
        direction = c1.selectbox("Direction", ["up", "down", "flat"], key=f"{k}_dir")
        min_pct = c2.number_input("Min %", min_value=0.0, value=0.0, step=0.1, key=f"{k}_min")
        max_pct = c3.number_input("Max %", min_value=0.0, value=0.5, step=0.1, key=f"{k}_max")
        return GapCondition(direction=direction, min_pct=min_pct, max_pct=max_pct)
    if condition_type == "VIX":
        c1, c2 = st.columns(2)
        operator = c1.selectbox("Operator", OPERATORS, index=0, key=f"{k}_op")
        threshold = c2.number_input("Threshold", value=15.0, step=0.5, key=f"{k}_thr")
        return VixCondition(operator=operator, threshold=threshold)
    if condition_type == "ATM straddle %":
        c1, c2 = st.columns(2)
        operator = c1.selectbox("Operator", OPERATORS, index=1, key=f"{k}_op")
        threshold_pct = c2.number_input("Threshold %", value=1.0, step=0.1, key=f"{k}_thr")
        return StraddlePctCondition(operator=operator, threshold_pct=threshold_pct)
    if condition_type == "Option premium change":
        c1, c2, c3 = st.columns(3)
        reference = c1.selectbox("Vs.", premium_references, key=f"{k}_ref")
        operator = c2.selectbox("Operator", OPERATORS, index=1, key=f"{k}_op")
        pct = c3.number_input("% change", value=100.0, step=10.0, key=f"{k}_pct")
        return PremiumChangeCondition(reference=reference, operator=operator, pct=pct)
    if condition_type == "Greek":
        c1, c2, c3 = st.columns(3)
        greek = c1.selectbox("Greek", ["delta", "gamma", "theta", "vega"], key=f"{k}_greek")
        operator = c2.selectbox("Operator", OPERATORS, index=0, key=f"{k}_op")
        threshold = c3.number_input("Threshold", value=0.0, step=0.01, format="%.4f", key=f"{k}_thr")
        return GreekCondition(greek=greek, operator=operator, threshold=threshold)
    if condition_type == "IV regime":
        c1, c2 = st.columns(2)
        regime = c1.selectbox("Regime", ["rich", "cheap", "neutral"], key=f"{k}_regime")
        neutral_band = c2.number_input("Neutral band", value=0.02, step=0.01, format="%.2f", key=f"{k}_band")
        return IvRegimeCondition(regime=regime, neutral_band=neutral_band)
    raise ValueError(f"Unknown condition type {condition_type!r}")


def render_condition_set(
    key_prefix: str,
    title: str,
    help_text: str,
    premium_references: list[str],
    default_if_empty: Condition,
) -> Condition:
    """Renders the row editor under ``title`` and returns the assembled
    condition: ``default_if_empty`` with zero rows, that single row's own
    condition with exactly one row, or an ``And``/``Or`` over every row
    (an AND/OR radio appears only once there's more than one row to
    combine) with more than one.
    """
    st.markdown(f"**{title}**")
    st.caption(help_text)

    rows_key, next_id_key = f"{key_prefix}_rows", f"{key_prefix}_next_id"
    if rows_key not in st.session_state:
        st.session_state[rows_key] = []
        st.session_state[next_id_key] = 0
    rows = st.session_state[rows_key]

    conditions: list[Condition] = []
    for row in list(rows):
        row_id = row["id"]
        row_cols = st.columns([1, 3])
        with row_cols[0]:
            row["type"] = st.selectbox(
                "Condition", CONDITION_TYPES, index=CONDITION_TYPES.index(row["type"]),
                key=f"{key_prefix}_{row_id}_type",
            )
        with row_cols[1]:
            conditions.append(_row_condition(key_prefix, row_id, row["type"], premium_references))
        if st.button("Remove this condition", key=f"{key_prefix}_{row_id}_remove"):
            rows.remove(row)
            st.rerun()
        st.divider()

    if st.button("+ Add condition", key=f"{key_prefix}_add"):
        rows.append({"id": st.session_state[next_id_key], "type": CONDITION_TYPES[0]})
        st.session_state[next_id_key] += 1
        st.rerun()

    if not conditions:
        return default_if_empty
    if len(conditions) == 1:
        return conditions[0]

    match_mode = st.radio(
        "Combine these conditions with", ["AND (all must be true)", "OR (any one)"],
        horizontal=True, key=f"{key_prefix}_match_mode",
    )
    return And(tuple(conditions)) if match_mode.startswith("AND") else Or(tuple(conditions))
