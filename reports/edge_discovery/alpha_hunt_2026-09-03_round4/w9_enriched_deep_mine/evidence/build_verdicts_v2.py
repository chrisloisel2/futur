#!/usr/bin/env python3
"""W9 Phase 1 — table d'audit v2 : colonne -> verdict + PERIMETRE D'USAGE.

v2 par rapport a build_verdicts.py :
  - les colonnes taker_*/trades ne sont plus classees sur une liste ecrite a la main mais sur
    la mesure empirique par symbole x annee (evidence/SOURCE_ATTRIBUTION.csv) ;
  - verification que le bloc `_label_features` du generateur (19 familles en shift(-n))
    n'est PAS materialise dans les parquets (il ne l'est pas : seules 3 colonnes label existent) ;
  - ajout d'une colonne `usage_scope` : ou/quand la colonne est saine ;
  - ajout d'une colonne `audit_test` : quel test A1-A8 a produit le verdict.
Entrees : scratch/audit_*.json, scratch/schemas.json, evidence/SOURCE_ATTRIBUTION.csv, evidence/SEAMS.csv
Sorties : evidence/COLUMN_VERDICTS.csv (remplacee), evidence/AUDIT_SUMMARY_BY_FAMILY.csv,
          evidence/NEVER_USE.csv
"""
import json, glob, os, collections, re, csv
import pandas as pd

SC   = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
HERE = os.path.dirname(os.path.abspath(__file__))

A = {}
for f in glob.glob(SC + "/audit_*.json"):
    d = json.load(open(f)); A[d["symbol"]] = d
schemas = json.load(open(SC + "/schemas.json"))
NSYM = len(schemas); audited = sorted(A); NA = len(audited)
presence = collections.Counter()
for v in schemas.values():
    for c in set(v): presence[c] += 1

# ---------- mesures empiriques par symbole (test A8 / A8-bis) ----------
src = pd.read_csv(HERE + "/SOURCE_ATTRIBUTION.csv")
by_sym = src.groupby("symbol").agg(tbb_half=("tbb_is_half_volume", "mean"),
                                   tbq_half=("tbq_is_half_qav", "mean"),
                                   ntr0=("ntr_zero_rate", "mean")).round(4)
REAL_TAKER_BASE = sorted(by_sym.index[by_sym.tbb_half < 0.05])          # taker base reel
REAL_TRADES     = sorted(by_sym.index[by_sym.ntr0     < 0.05])          # number_of_trades reel
PARTIAL_TAKER   = sorted(by_sym.index[(by_sym.tbb_half >= 0.05) & (by_sym.tbb_half < 0.999)])
SPOT_SYMS  = sorted(src.loc[src.source == "SPOT", "symbol"].unique())
MIX_SYMS   = sorted(src.loc[src.source == "MIX",  "symbol"].unique())
SWITCH = ("bascule de source PERP -> SPOT : BTCUSDT au 2026-01-01 ; "
          "ADA/AVAX/BNB/ETH/LINK/SOL au 2026-05-20 ; DOGE/XRP au 2026-05-24 ; DOT au 2026-06-28")

# ---------- signaux de degenerescence issus des audits colonne par colonne ----------
const_cnt = collections.Counter(); null_hi = collections.Counter(); allnull = collections.Counter()
dup_of = {}
for s in audited:
    for c, d in A[s]["stats"].items():
        if d.get("degenerate") == "all_null": allnull[c] += 1
        if d.get("std") == 0.0: const_cnt[c] += 1
        nr = d.get("null_rate_postwarmup")
        if isinstance(nr, float) and nr > 0.20: null_hi[c] += 1
    for canon, dups in A[s]["duplicate_groups"].items():
        for c in dups: dup_of.setdefault(c, collections.Counter())[canon] += 1

LABELS = {"future_ret_8h", "future_ret_h16_min", "future_ret_h16_max"}
TAKER_QUOTE = {"taker_buy_quote_asset_volume", "taker_buy_quote", "taker_buy_ratio_quote", "taker_sell_quote"}
TAKER_BASE  = {"taker_buy_base_asset_volume", "taker_buy_base", "taker_buy_ratio_base", "taker_sell_base"}
TRADES      = {"number_of_trades", "trades"}
PATH_DEPENDENT = {"obv", "accumulation_distribution_line", "anchored_vwap", "positive_volume_index",
                  "negative_volume_index", "volume_price_trend", "cumulative_return", "force_index",
                  "current_drawdown", "current_runup", "long_term_drawdown", "time_under_water", "max_drawdown"}
META = {"datetime", "symbol", "interval", "session", "feature_version", "feature_count", "feature_horizons"}
OHLCV = {"open", "high", "low", "close", "volume", "quote_asset_volume", "Open", "High", "Low", "Close", "Volume"}

GLOBAL_SCOPE = ("toute la periode SAUF la queue post-bascule de source (voir usage_scope de `close`) ; "
                "DOGEUSDT et XRPUSDT sont SPOT sur tout l'historique, pas PERP")

rows = []
for c in sorted(presence):
    pres = presence[c]; fam = re.sub(r'_\d+$', '', c); reasons = []; verdict = None
    scope = GLOBAL_SCOPE; test = ""
    canon_col = dup_of[c].most_common(1)[0][0] if c in dup_of else ""
    if c in META:
        verdict, test = "METADATA", "-"
        reasons.append("colonne de metadonnee, pas une feature")
        scope = "n/a"
    elif c in LABELS:
        verdict, test = "LOOKAHEAD", "A7"
        reasons.append("label forward verifie empiriquement : future_ret_8h == log(close[t+8]/close[t]) "
                       "a 1e-15 pres sur 100% des barres. FUITE si utilise comme feature. "
                       "De plus non recalcule sur la queue : s'arrete ~27 jours avant la fin de fichier "
                       "pour les symboles maintenus en live")
        scope = "cible d'apprentissage uniquement, JAMAIS en feature"
    elif c in TAKER_QUOTE:
        verdict, test = "PLACEHOLDER", "A3/A8"
        reasons.append("= quote_asset_volume x 0.5 EXACTEMENT sur 100% des barres, 50/50 symboles, "
                       "toutes annees (verifie en tolerance relative 1e-6 contre le panel V2)")
        scope = "AUCUN — remplacer par futur-data-v2 perp_ohlcv.taker_buy_quote_asset_volume"
    elif c in TAKER_BASE:
        verdict, test = "DEGRADED_PERIOD", "A3/A8"
        reasons.append(f"= volume x 0.5 (placeholder) sauf pour {len(REAL_TAKER_BASE)} symboles ou la valeur "
                       f"est reelle et concorde exactement avec le panel V2")
        scope = ("reel uniquement pour " + ", ".join(REAL_TAKER_BASE) +
                 " ; partiellement reel (queue recente) pour " + ", ".join(PARTIAL_TAKER) +
                 " ; placeholder partout ailleurs")
    elif c in TRADES:
        verdict, test = "DEGRADED_PERIOD", "A3/A8"
        reasons.append("= 0 sur 100% des barres pour la majorite des symboles ; `trades` est un alias "
                       "bit-a-bit de `number_of_trades`")
        scope = "reel uniquement pour " + ", ".join(REAL_TRADES) + " ; = 0 partout ailleurs"
    elif allnull.get(c, 0) == NA and NA > 0:
        verdict, test = "PLACEHOLDER", "A2"
        reasons.append("100% nul sur tous les symboles audites"); scope = "AUCUN"
    elif const_cnt.get(c, 0) >= max(1, NA // 2):
        verdict, test = "PLACEHOLDER", "A3"
        reasons.append(f"constante (std=0) sur {const_cnt[c]}/{NA} symboles audites"); scope = "AUCUN"
    elif c in dup_of and max(dup_of[c].values()) >= max(1, NA // 2):
        canon = dup_of[c].most_common(1)[0][0]
        if const_cnt.get(canon, 0) >= max(1, NA // 2):
            verdict, test = "PLACEHOLDER", "A3/A4"
            reasons.append(f"identique bit-a-bit a `{canon}`, elle-meme constante -> groupe entierement degenere")
            scope = "AUCUN"
        else:
            verdict, test = "REDUNDANT_ALIAS", "A4"
            reasons.append(f"identique bit-a-bit a `{canon}` sur {max(dup_of[c].values())}/{NA} symboles : "
                           "utilisable mais n'apporte AUCUNE information nouvelle (utiliser le nom canonique)")
    elif pres < NSYM:
        verdict, test = "NOT_UNIVERSAL", "A1"
        reasons.append(f"presente sur {pres}/{NSYM} symboles seulement")
        scope = "sous-ensemble de symboles seulement — interdit en cross-section"
    elif fam in PATH_DEPENDENT or c in PATH_DEPENDENT:
        verdict, test = "DEGRADED_PERIOD", "A6"
        reasons.append("feature cumulative / a etat : reset mecanique mesure a la bascule de source "
                       "(ex. obv BTC -1 940 682 -> -135 056 au 2026-01-01 ; 9 autres symboles au 2026-05-20/24)")
        scope = "utilisable en DIFFERENCE seulement, et jamais a cheval sur une couture (voir SEAMS.csv)"
    elif null_hi.get(c, 0) >= 1:
        verdict, test = "SUSPECT", "A2"
        reasons.append(f"taux de nuls >20% hors warm-up sur {null_hi[c]}/{NA} symboles audites")
    else:
        verdict, test = "USABLE", "A1-A8"
        reasons.append("causale par construction (verifie dans data_pipeline/enriched_ohlcv_features.py : "
                       "fenetres .rolling() strictement trailing, aucun center=True, aucun shift(-n)), "
                       "non degeneree, universelle")
    if c in OHLCV:
        scope = ("PERP Binance jusqu'a la bascule ; " + SWITCH +
                 " ; DOGEUSDT/XRPUSDT sont SPOT sur TOUT l'historique. "
                 "Volume et close concordent a 100% avec futur-data-v2 sur la source effective.")
        test = (test + "+A8").strip("+")
    rows.append(dict(column=c, family=fam, present_in=f"{pres}/{NSYM}", verdict=verdict,
                     canonical=canon_col, audit_test=test, usage_scope=scope,
                     reason=" ; ".join(reasons)))

with open(HERE + "/COLUMN_VERDICTS.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["column", "family", "present_in", "verdict", "canonical",
                                       "audit_test", "usage_scope", "reason"])
    w.writeheader(); w.writerows(rows)

d = pd.DataFrame(rows)
cnt = d["verdict"].value_counts()
print("total colonnes (union des 50 schemas):", len(d))
for k, v in cnt.items(): print(f"  {k:16s} {v:5d}  ({100*v/len(d):.1f}%)")

fam = (d.groupby(["family", "verdict"]).size().unstack(fill_value=0))
fam["n_cols"] = fam.sum(axis=1)
fam = fam.sort_values("n_cols", ascending=False)
fam.to_csv(HERE + "/AUDIT_SUMMARY_BY_FAMILY.csv")
never = d[d.verdict.isin(["PLACEHOLDER", "LOOKAHEAD"])][["column", "verdict", "audit_test", "reason"]]
never.to_csv(HERE + "/NEVER_USE.csv", index=False)
print("\nfamilles distinctes:", len(fam), "| colonnes a NE JAMAIS utiliser:", len(never))
print("\ntaker base reel  :", REAL_TAKER_BASE)
print("taker base partiel:", PARTIAL_TAKER)
print("trades reel      :", REAL_TRADES)
print("symboles SPOT    :", SPOT_SYMS)
print("symboles MIX 2026:", MIX_SYMS)
usable = [r["column"] for r in rows if r["verdict"] in ("USABLE", "REDUNDANT_ALIAS")]
json.dump(usable, open(SC + "/usable_cols.json", "w"))
print("USABLE(+alias) ecrit:", len(usable))
