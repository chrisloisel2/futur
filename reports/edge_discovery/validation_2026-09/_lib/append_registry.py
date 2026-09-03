"""Ajoute au registre les candidats wave 2 qui n'y figuraient pas encore
(#12, #13, #18, #19 de la liste de mission — inventoriés au round 4 / hors round 1-3).
Idempotent : n'ajoute que ce qui manque."""
from __future__ import annotations

import json
import os
import re

REG = "/home/qbee/futur/configs/validation_registry.yaml"
BASE = "/home/qbee/futur/reports/edge_discovery/validation_2026-09"

NEW = {
    "OI_COLLAPSE_BOUNCE": {
        "family": "liquidation",
        "overlap_family": "LIQ_CASCADE_DETECTOR",
        "mechanism": (
            "Effondrement extrême de l'open interest sur 24 h au moment d'une cascade LONG : "
            "le déleveraging forcé est terminé, le prix rebondit. Conditionnement du fade de "
            "cascade, pas un détecteur nouveau."),
        "source": "round4 / liste de mission #12",
        "mission_rank": 12,
    },
    "CVD_SHOCK_DOWN_MEMORY": {
        "family": "liquidation",
        "overlap_family": "LIQ_CASCADE_DETECTOR",
        "mechanism": (
            "Choc baissier du déséquilibre de flux taker (proxy CVD) au moment d'une cascade : "
            "mémoire du choc supposée porter une reprise."),
        "source": "round4 / liste de mission #13",
        "mission_rank": 13,
    },
    "PREMIUM_EXTREME_THEN_CASCADE": {
        "family": "liquidation",
        "overlap_family": "PREMIUM_DISLOCATION",
        "mechanism": (
            "Premium index en dislocation extrême (queue basse causale) suivi d'une cascade : "
            "la dislocation perp/spot se referme."),
        "source": "round3 W1 c03 / liste de mission #18",
        "mission_rank": 18,
    },
    "CROWD_WASHOUT_NO_CASCADE": {
        "family": "positioning",
        "overlap_family": "CROWDING",
        "mechanism": (
            "Capitulation du positionnement des top traders SANS cascade de liquidation "
            "associée — washout de foule pur."),
        "source": "round3 W1 c08 / liste de mission #19",
        "mission_rank": 19,
    },
}


def wrap(text, width=88):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def sc(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return f'"{str(v)}"'


def main():
    src = open(REG).read()
    existing = set(re.findall(r"^  - candidate_id: (\S+)\s*$", src, re.M))
    chunks = []
    for cid, meta in NEW.items():
        if cid in existing:
            continue
        p = f"{BASE}/{cid}/RESULTS.json"
        if not os.path.exists(p):
            continue
        r = json.load(open(p))
        lines = [
            "",
            f"  - candidate_id: {cid}",
            f"    family: {meta['family']}",
            f"    overlap_family: {meta['overlap_family']}",
            "    economic_mechanism: >",
            *[f"      {x}" for x in wrap(meta["mechanism"])],
            f"    source_round: {meta['source']}",
            f"    mission_list_rank: {meta['mission_rank']}",
            f"    discovery_result: \"réclamation liste de mission : {r['discovery_net_bps']} bps net\"",
            f"    current_status: {r['verdict']}   # wave 2, 2026-09-03",
            "    existing_live_alpha: null",
            "    data_available: \"data/events/{cascade,premium,crowding}_dataset.parquet\"",
            "    execution_available: true",
            "    wave2_validated_at: \"2026-09-03\"   # Alpha Validation Factory wave 2, worker unique (session futur-49)",
            f"    validated_for_forward: {sc(r['validated_for_forward'])}",
            f"    confirmable_in_horizon: {sc(r['confirmable_in_horizon'])}",
            f"    sign_correction_required: {sc(r['sign_correction_required'])}",
            f"    validation_report: {r['validation_report'].replace('/home/qbee/futur/', '')}",
            f"    discovery_net_bps: {sc(r['discovery_net_bps'])}",
            f"    validation_net_bps: {sc(r['validation_net_bps'])}",
            f"    validation_net_bps_stress28: {sc(r['validation_net_bps_stress28'])}",
            f"    n_validation_independent: {sc(r['n_validation_independent'])}   # {r['l3_definition']}",
            f"    t_stat_declustered: {sc(r['t_stat_declustered'])}",
            f"    eta_conservative: {sc(r['eta_conservative'])}",
            f"    recommended_next_step: {r['recommended_next_step']}",
            f"    secondary_tags: {json.dumps(r['secondary_tags'])}",
            "    validation_result: >",
            *[f"      {x}" for x in wrap(r["validation_caveats"])],
        ]
        chunks.append("\n".join(lines))

    if not chunks:
        print("rien à ajouter")
        return
    header = (
        "\n  # ═════════════ WAVE 2 — candidats hors round 1-3 (liste de mission #12/13/18/19) ═════════════\n"
        "  # Inventoriés ET validés dans la même passe (2026-09-03) : ils n'étaient dans aucun\n"
        "  # registre auparavant. Même gate que le reste de la vague.\n"
    )
    open(REG, "a").write(header + "\n".join(chunks) + "\n")
    print(f"ajouté {len(chunks)} candidats")


if __name__ == "__main__":
    main()
