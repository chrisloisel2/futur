#!/usr/bin/env python
"""T1c — Gate §2 complet sur LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION,
dans les DEUX directions preenregistrees :

  SSE_MEANREV (hypothese principale) : SHORT — symetrique exact du mecanisme
      LIQ_CASCADE_REPEAT_V1 (se positionner CONTRE le flux force epuise).
      Etabli par T1a+T1b : SHORT_SQUEEZE == achats forces (shorts liquides).
  SSE_CONT    (hypothese secondaire) : LONG — ce que le pipeline fige mesure
      reellement (fwd_4h brut), i.e. le chiffre +40.0/+114.6bps du round 2.

Controle : LONG_CASCADE exhaustion en LONG = l'alpha deja en shadow.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate import gate, contrast  # noqa: E402

ROOT = Path("/home/qbee/futur")
OUT = Path(__file__).resolve().parent


def main():
    df = pd.read_parquet(ROOT / "data/events/liq_cascade_dataset.parquet")
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df = df[df["label_full"] & df["fwd_4h"].notna()].copy()
    # ⚠ CORRECTION DE SPEC (documentee, pas un refit) : le chiffre publie du round 2
    # (+40.0 full / +114.6 OOS, n=1140/350) correspond a n_events_sym_24h >= 3, PAS >= 2.
    # Verifie par reproduction exacte des N (LONG_CASCADE 1988, SHORT_SQUEEZE 1140).
    # Le code fige repeat_variant.py utilise >= 2. Les DEUX sont testes.
    THR = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    df["bucket"] = pd.cut(df["n_events_sym_24h"], [-1, 0, THR - 1, 10 ** 9],
                          labels=["onset", "mid", "exhaustion"])

    res = {"dataset": "data/events/liq_cascade_dataset.parquet",
           "exhaustion_threshold_n_events_sym_24h_ge": THR,
           "n_total_labelled": int(len(df)),
           "period": [str(df["event_time"].min()), str(df["event_time"].max())],
           "counts": {}}
    for k, g in df.groupby(["kind", "bucket"], observed=True):
        res["counts"][f"{k[0]}|{k[1]}"] = int(len(g))

    ss = df[df["kind"] == "SHORT_SQUEEZE"]
    lc = df[df["kind"] == "LONG_CASCADE"]
    ss_ex, ss_on = ss[ss.bucket == "exhaustion"], ss[ss.bucket == "onset"]
    lc_ex, lc_on = lc[lc.bucket == "exhaustion"], lc[lc.bucket == "onset"]

    res["gates"] = {
        "SSE_CONT_exhaustion_LONG": gate(ss_ex, direction="LONG", label="SHORT_SQUEEZE exhaustion, LONG (round2 spec)"),
        "SSE_MEANREV_exhaustion_SHORT": gate(ss_ex, direction="SHORT", label="SHORT_SQUEEZE exhaustion, SHORT (symetrique)"),
        "SS_onset_LONG": gate(ss_on, direction="LONG", label="SHORT_SQUEEZE onset, LONG"),
        "SS_onset_SHORT": gate(ss_on, direction="SHORT", label="SHORT_SQUEEZE onset, SHORT"),
        "CONTROL_LC_exhaustion_LONG": gate(lc_ex, direction="LONG", label="LONG_CASCADE exhaustion, LONG (alpha shadow existant)"),
        "CONTROL_LC_onset_LONG": gate(lc_on, direction="LONG", label="LONG_CASCADE onset, LONG"),
    }
    res["contrasts"] = {
        "SS_exhaustion_minus_onset_LONG": contrast(ss_ex, ss_on, "fwd_4h", "LONG",
                                                   "SHORT_SQUEEZE exh - onset (LONG)"),
        "SS_exhaustion_minus_onset_SHORT": contrast(ss_ex, ss_on, "fwd_4h", "SHORT",
                                                    "SHORT_SQUEEZE exh - onset (SHORT)"),
        "CONTROL_LC_exhaustion_minus_onset_LONG": contrast(lc_ex, lc_on, "fwd_4h", "LONG",
                                                           "LONG_CASCADE exh - onset (LONG)"),
        "SS_exhaustion_LONG_minus_LC_exhaustion_LONG": contrast(ss_ex, lc_ex, "fwd_4h", "LONG",
                                                                "SS exh - LC exh (LONG, cross-kind)"),
    }

    # ── controle de derive : rendement inconditionnel du marche sur 4h ──
    allq = df.copy()
    res["unconditional_all_events_LONG"] = gate(allq, direction="LONG",
                                                label="TOUS events, LONG (baseline de derive)")

    (OUT / f"t1c_short_squeeze_gate_thr{THR}.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
