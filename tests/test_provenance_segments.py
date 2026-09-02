"""
tests/test_provenance_segments.py — item P1 (phase OPERATIONAL HARDENING) :
pre/post execution-truth-fix segment tagging (commit ed17708). Ne réécrit
jamais l'historique pré-fix, mais empêche de le mélanger silencieusement
avec le post-fix dans une comparaison scientifique.
"""
from __future__ import annotations

import pandas as pd

from src.institutional.live_alpha_lab.provenance import (
    EXECUTION_TRUTH_FIX_DEPLOYED_AT, POST_EXECUTION_TRUTH_FIX,
    PRE_EXECUTION_TRUTH_FIX, execution_truth_fix_segment,
)


def test_before_fix_deploy_is_pre_segment():
    ts = EXECUTION_TRUTH_FIX_DEPLOYED_AT - pd.Timedelta(seconds=1)
    assert execution_truth_fix_segment(ts) == PRE_EXECUTION_TRUTH_FIX


def test_after_fix_deploy_is_post_segment():
    ts = EXECUTION_TRUTH_FIX_DEPLOYED_AT + pd.Timedelta(seconds=1)
    assert execution_truth_fix_segment(ts) == POST_EXECUTION_TRUTH_FIX


def test_exact_deploy_timestamp_is_post_segment():
    assert execution_truth_fix_segment(EXECUTION_TRUTH_FIX_DEPLOYED_AT) == POST_EXECUTION_TRUTH_FIX


def test_handles_naive_timestamp_without_crashing():
    naive = pd.Timestamp("2026-09-01T00:00:00")   # pas de timezone
    assert execution_truth_fix_segment(naive) == PRE_EXECUTION_TRUTH_FIX


def test_handles_iso_string_input():
    assert execution_truth_fix_segment("2026-09-01T00:51:46.101525+00:00") == PRE_EXECUTION_TRUTH_FIX


def test_known_real_divergence_timestamp_tags_as_pre_fix():
    """Le premier point de divergence RÉEL trouvé entre P1_EQUAL_RISK et
    P1_CONTROL (root-cause P0.1) doit être tagué PRE_EXECUTION_TRUTH_FIX --
    c'est exactement l'artefact que ce mécanisme doit isoler."""
    real_divergence_ts = "2026-09-01T00:51:46.101525+00:00"
    assert execution_truth_fix_segment(real_divergence_ts) == PRE_EXECUTION_TRUTH_FIX
