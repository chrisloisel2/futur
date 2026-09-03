"""Reporte les verdicts wave 2 dans configs/validation_registry.yaml.

Édition CHIRURGICALE : on remplace la ligne `current_status:` du bloc du candidat et
on insère les champs de validation juste après, sans toucher au reste du fichier
(les commentaires du registre sont du contenu, pas du bruit).

Idempotent : relancer le script ne duplique pas les champs déjà présents.
"""
from __future__ import annotations

import json
import os
import re

REG = "/home/qbee/futur/configs/validation_registry.yaml"
BASE = "/home/qbee/futur/reports/edge_discovery/validation_2026-09"

# candidats produits par cette vague (ceux qui ont un RESULTS.json)
WAVE2 = [
    "BTC_LEAD_ALT_CASCADE", "LIQ_CASCADE_FAR_FROM_LOW", "XSEC_RESIDUAL_MOMENTUM_14D",
    "XSEC_MOMENTUM_HORIZON_EXTENSION", "SECTOR_ROTATION",
    "SECTOR_RELATIVE_STRENGTH_REVERSAL", "OI_COLLAPSE_BOUNCE",
    "CVD_SHOCK_DOWN_MEMORY", "PREMIUM_EXTREME_THEN_CASCADE", "CROWD_WASHOUT_NO_CASCADE",
]

MARK = "wave2_validated_at"          # sentinelle d'idempotence


def yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace('"', "'")
    return f'"{s}"'


def block_for(r: dict) -> list[str]:
    """Champs de validation à insérer sous current_status."""
    return [
        f"    {MARK}: \"2026-09-03\"   # Alpha Validation Factory wave 2, worker unique (session futur-49)",
        f"    validated_for_forward: {yaml_scalar(r['validated_for_forward'])}",
        f"    confirmable_in_horizon: {yaml_scalar(r['confirmable_in_horizon'])}",
        f"    sign_correction_required: {yaml_scalar(r['sign_correction_required'])}",
        f"    validation_report: {r['validation_report'].replace('/home/qbee/futur/', '')}",
        f"    discovery_net_bps: {yaml_scalar(r['discovery_net_bps'])}",
        f"    validation_net_bps: {yaml_scalar(r['validation_net_bps'])}",
        f"    validation_net_bps_stress28: {yaml_scalar(r['validation_net_bps_stress28'])}",
        f"    n_validation_independent: {yaml_scalar(r['n_validation_independent'])}   # {r['l3_definition']}",
        f"    t_stat_declustered: {yaml_scalar(r['t_stat_declustered'])}",
        f"    eta_conservative: {yaml_scalar(r['eta_conservative'])}",
        f"    recommended_next_step: {r['recommended_next_step']}",
        f"    secondary_tags: {json.dumps(r['secondary_tags'])}",
        f"    validation_result: >",
    ] + [f"      {line}" for line in wrap(r["validation_caveats"], 88)]


def wrap(text: str, width: int) -> list[str]:
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


def main() -> None:
    src = open(REG).read().splitlines()
    results = {}
    for cid in WAVE2:
        p = f"{BASE}/{cid}/RESULTS.json"
        if os.path.exists(p):
            results[cid] = json.load(open(p))

    out, i, n_updated, n_skipped = [], 0, 0, 0
    while i < len(src):
        line = src[i]
        m = re.match(r"^  - candidate_id: (\S+)\s*$", line)
        if not m or m.group(1) not in results:
            out.append(line)
            i += 1
            continue

        cid = m.group(1)
        r = results[cid]
        # borne du bloc : jusqu'au prochain `  - candidate_id:` ou fin de fichier
        j = i + 1
        while j < len(src) and not re.match(r"^  - candidate_id: ", src[j]):
            j += 1
        block = src[i:j]

        if any(MARK in b for b in block):
            out.extend(block)
            n_skipped += 1
            i = j
            continue

        new_block = []
        for b in block:
            if re.match(r"^    current_status: ", b):
                new_block.append(f"    current_status: {r['verdict']}   # wave 2, 2026-09-03")
                new_block.extend(block_for(r))
            else:
                new_block.append(b)
        out.extend(new_block)
        n_updated += 1
        i = j

    open(REG, "w").write("\n".join(out) + "\n")
    print(f"registre mis à jour : {n_updated} candidats, {n_skipped} déjà à jour")


if __name__ == "__main__":
    main()
