#!/usr/bin/env python3
"""
scripts/preregistration_status.py
─────────────────────────────────────────────────────────────────────────────
L'état du pré-enregistrement, en une commande.

    python3 scripts/preregistration_status.py
    python3 scripts/preregistration_status.py --extra 5     # « et si j'en testais cinq de plus ? »

La deuxième forme est celle qui compte. Elle existe pour qu'on puisse REGARDER
ce que coûte d'élargir la recherche avant de le payer, plutôt que de le
découvrir après — c'est-à-dire jamais, puisque personne ne recalcule un seuil
qu'il a déjà franchi.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.preregistration import (
    LEDGER,
    MAX_HYPOTHESES_PER_BATCH,
    current_threshold,
    retroactive_penalty,
    threshold_t,
    verify_ledger,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=str(LEDGER))
    parser.add_argument("--extra", type=int, default=0,
                        help="combien d'hypothèses de plus on envisage de sceller")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    ledger = Path(args.ledger)
    chain = verify_ledger(ledger)
    state = current_threshold(ledger, extra=args.extra)
    penalty = retroactive_penalty(ledger)

    if args.json:
        print(json.dumps({"chain": chain, "state": state, "penalty": penalty},
                         indent=2, ensure_ascii=False))
        return 0 if chain.get("ok") else 1

    print()
    if not chain.get("ok"):
        print("⛔ CHAÎNE ROMPUE : %s" % chain.get("error"))
        print("   Le ledger a été modifié après scellement. Rien de ce qui suit n'est fiable.")
        return 1

    n = state["n_registered"]
    if not n:
        print("Aucune hypothèse scellée.")
        print("Le premier lot en scellera au plus %d, et son seuil sera t > %.2f."
              % (MAX_HYPOTHESES_PER_BATCH, threshold_t(MAX_HYPOTHESES_PER_BATCH)))
        print()
        return 0

    print("Hypothèses scellées : %d   (chaîne vérifiée, %d lot(s))" % (n, chain["batches"]))
    print("Seuil en vigueur    : **t > %.4f**   (Bonferroni unilatéral, alpha famille %.2f)"
          % (state["threshold_t"], state["alpha_family"]))
    print()

    if len(penalty["batches"]) > 1:
        print("Ce que les lots déjà scellés doivent désormais :")
        for b in penalty["batches"]:
            arrow = "" if b["penalty_t"] <= 1e-9 else "   ⚠️ +%.3f depuis son scellement" % b["penalty_t"]
            print("  %-16s %d hypothèse(s)   seuil au scellement %.3f -> aujourd'hui %.3f%s"
                  % (b["batch_id"], b["n_hypotheses"], b["threshold_at_seal"],
                     b["threshold_now"], arrow))
        print()

    if args.extra:
        print("Si %d hypothèse(s) de plus étaient scellées :" % args.extra)
        print("  compte      %d  ->  %d" % (n, state["n_if_extended"]))
        print("  seuil       %.4f  ->  **%.4f**   (+%.4f)"
              % (state["threshold_t"], state["threshold_t_if_extended"],
                 state["cost_of_extension_t"]))
        print()
        print("  Ce surcoût s'applique AUSSI aux %d hypothèses déjà scellées. Un mécanisme" % n)
        print("  qui affichait t = %.2f et passait ne passerait plus." % state["threshold_t"])
        print()
        print("  C'est le prix de « juste cinq de plus pour voir ». Il est payable — rien")
        print("  ne l'interdit — mais il se paie sur tout ce qui a déjà été testé, pas")
        print("  seulement sur les nouvelles.")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
