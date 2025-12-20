def level3_decision(
    event_probs,
    pairwise_probs,
    thresholds=None,
):
    """
    Retourne: CONFIRM | INVALIDATE | DELAY
    """

    if thresholds is None:
        thresholds = {
            "event_strong": 0.6,
            "pairwise_contradiction": 0.55,
            "pairwise_weakening": 0.5,
        }

    p_event_up = event_probs[1]
    p_event_down = event_probs[2]
    p_vol_shock = event_probs[3]

    p_consistent = pairwise_probs[0]
    p_weak = pairwise_probs[1]
    p_contra = pairwise_probs[2]

    # ❌ INVALIDATE
    if p_contra > thresholds["pairwise_contradiction"]:
        return "INVALIDATE"

    # ⏳ DELAY
    if (
        p_vol_shock > thresholds["event_strong"]
        or p_weak > thresholds["pairwise_weakening"]
    ):
        return "DELAY"

    # ✅ CONFIRM
    return "CONFIRM"
