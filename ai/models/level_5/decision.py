def level3_decision(
    event_probs,
    pairwise_probs,
    edge=None,
    thresholds=None,
):
    """
    Décision — version corrigée

    Rôle:
    - CONFIRM / DELAY / INVALIDATE
    - Basée sur: stabilité du régime (event), cohérence inter-signaux (pairwise),
      et présence d'un edge suffisant (edge = sortie Level 2).

    Contrat d'entrée:
    - event_probs = [p_no_event, p_vol_shock, p_noise] OU [p_no_event, p_up, p_down, p_vol_shock]
      -> La direction (up/down) n'est PLUS utilisée ici.
    - pairwise_probs = [p_consistent, p_weak, p_contradict]
    - edge (optionnel mais recommandé) = dict ou float:
        * si dict: {"value": float, "confidence": float (0..1), "sign": -1/0/+1 (optionnel)}
        * si float: valeur signée de l'edge

    Sortie:
    - "INVALIDATE" | "DELAY" | "CONFIRM"
    """

    if thresholds is None:
        thresholds = {
            # Event / contexte
            "event_vol_shock": 0.60,     # au-dessus => DELAY
            "event_no_event": 0.80,      # au-dessus => DELAY (rien à exploiter)

            # Pairwise (cohérence)
            "pairwise_contradiction": 0.55,  # au-dessus => INVALIDATE
            "pairwise_weakening": 0.50,      # au-dessus => DELAY
            "min_consistency": 0.45,         # en-dessous => DELAY

            # Edge (Level 2)
            "edge_min_abs": 0.10,        # |edge| trop faible => DELAY
            "edge_min_conf": 0.55,       # confiance edge trop faible => DELAY
        }

    # -------------------------
    # Unpack pairwise
    # -------------------------
    p_consistent = float(pairwise_probs[0])
    p_weak = float(pairwise_probs[1])
    p_contra = float(pairwise_probs[2])

    # -------------------------
    # Unpack event (robuste aux 2 formats)
    # -------------------------
    event_probs = list(event_probs)
    if len(event_probs) == 4:
        # Ancien format: [no_event, up, down, vol_shock]
        p_no_event = float(event_probs[0])
        p_vol = float(event_probs[3])
    elif len(event_probs) >= 2:
        # Nouveau format minimal: [no_event, vol_shock, ...]
        p_no_event = float(event_probs[0])
        p_vol = float(event_probs[1])
    else:
        p_no_event = 0.0
        p_vol = 0.0

    # -------------------------
    # Edge parsing
    # -------------------------
    edge_value = None
    edge_conf = None

    if isinstance(edge, dict):
        edge_value = float(edge.get("value", 0.0))
        edge_conf = float(edge.get("confidence", 1.0))
    elif edge is None:
        edge_value = None
        edge_conf = None
    else:
        # float / int
        edge_value = float(edge)
        edge_conf = 1.0

    # =========================
    # 1) INVALIDATE — priorité absolue
    # =========================
    if p_contra >= thresholds["pairwise_contradiction"]:
        return "INVALIDATE"

    # =========================
    # 2) DELAY — instabilité / bruit / manque d'edge
    # =========================

    # Contexte non exploitable (vol shock)
    if p_vol >= thresholds["event_vol_shock"]:
        return "DELAY"

    # Pas d'événement détectable -> pas de raison d'insister
    if p_no_event >= thresholds["event_no_event"]:
        return "DELAY"

    # Signal affaibli inter-modèles
    if p_weak >= thresholds["pairwise_weakening"]:
        return "DELAY"

    # Cohérence globale insuffisante
    if p_consistent < thresholds["min_consistency"]:
        return "DELAY"

    # Pas d'edge fourni ou edge trop faible
    if edge_value is None:
        return "DELAY"

    if abs(edge_value) < thresholds["edge_min_abs"]:
        return "DELAY"

    if edge_conf is not None and edge_conf < thresholds["edge_min_conf"]:
        return "DELAY"

    # =========================
    # 3) CONFIRM — edge clair + contexte stable
    # =========================
    return "CONFIRM"
