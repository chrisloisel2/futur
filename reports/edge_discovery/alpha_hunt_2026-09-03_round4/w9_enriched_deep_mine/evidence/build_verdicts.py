#!/usr/bin/env python3
"""W9 Phase 1 — construit la table colonne -> verdict a partir des audits JSON."""
import json, glob, os, collections, re, csv
SC=os.environ.get("W9_OUT","/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
OUTD=os.environ.get("W9_DIR","/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w9_enriched_deep_mine")
A={}
for f in glob.glob(SC+"/audit_*.json"):
    d=json.load(open(f)); A[d["symbol"]]=d
schemas=json.load(open(SC+"/schemas.json"))
NSYM=len(schemas)
presence=collections.Counter()
for v in schemas.values():
    for c in set(v): presence[c]+=1
audited=sorted(A)
NA=len(audited)

# --- signaux par colonne
const_cnt=collections.Counter(); null_hi=collections.Counter(); allnull=collections.Counter()
dup_of={}
for s in audited:
    st=A[s]["stats"]
    for c,d in st.items():
        if d.get("degenerate")=="all_null": allnull[c]+=1
        if d.get("std")==0.0: const_cnt[c]+=1
        nr=d.get("null_rate_postwarmup")
        if isinstance(nr,float) and nr>0.20: null_hi[c]+=1
    for canon,dups in A[s]["duplicate_groups"].items():
        for c in dups: dup_of.setdefault(c,collections.Counter())[canon]+=1

TAKER_PLACEHOLDER={"taker_buy_quote_asset_volume","taker_buy_quote","taker_buy_ratio_quote","taker_sell_quote"}
TAKER_PARTIAL={"taker_buy_base_asset_volume","taker_buy_base","taker_buy_ratio_base","taker_sell_base"}
LABELS={"future_ret_8h","future_ret_h16_min","future_ret_h16_max"}
# features cumulatives / a etat, cassees par la bascule de source BTC 2026-01-01
PATH_DEPENDENT={"obv","accumulation_distribution_line","anchored_vwap","positive_volume_index",
                "negative_volume_index","volume_price_trend","cumulative_return","force_index",
                "current_drawdown","current_runup","long_term_drawdown","time_under_water","max_drawdown"}
META={"datetime","symbol","interval","session","feature_version","feature_count","feature_horizons"}

rows=[]
for c in sorted(presence):
    pres=presence[c]
    fam=re.sub(r'_\d+$','',c)
    reasons=[]; verdict=None
    if c in META:
        verdict="METADATA"; reasons.append("colonne de metadonnee, pas une feature")
    elif c in LABELS:
        verdict="LOOKAHEAD"; reasons.append("label forward (shift(-n)) : fuite si utilise comme feature ; de plus perime (non recalcule sur la queue appendee)")
    elif c in TAKER_PLACEHOLDER:
        verdict="PLACEHOLDER"; reasons.append("=quote_volume*0.5 exactement sur 100% des barres, 50/50 symboles (fallback generateur ligne 107)")
    elif c in TAKER_PARTIAL:
        verdict="DEGRADED_PERIOD"; reasons.append("placeholder volume*0.5 sauf 8 symboles (ADA AVAX BNB DOGE LINK SOL XRP integralement ; BTC/ETH/DOT seulement sur la queue 2026)")
    elif c in ("number_of_trades","trades"):
        verdict="DEGRADED_PERIOD"; reasons.append("=0 sur 100% des barres pour 40/50 symboles ; reel seulement pour les 10 symboles live")
    elif allnull.get(c,0)==NA and NA>0:
        verdict="PLACEHOLDER"; reasons.append("100% nul sur tous les symboles audites")
    elif const_cnt.get(c,0)>=max(1,NA//2):
        verdict="PLACEHOLDER"; reasons.append(f"constante (std=0) sur {const_cnt[c]}/{NA} symboles audites")
    elif c in dup_of and max(dup_of[c].values())>=max(1,NA//2):
        canon=dup_of[c].most_common(1)[0][0]
        if const_cnt.get(canon,0)>=max(1,NA//2):
            verdict="PLACEHOLDER"; reasons.append(f"identique bit-a-bit a `{canon}`, elle-meme constante -> groupe entierement degenere")
        else:
            verdict="REDUNDANT_ALIAS"; reasons.append(f"identique bit-a-bit a `{canon}` sur {max(dup_of[c].values())}/{NA} symboles : utilisable mais n'apporte AUCUNE information nouvelle (utiliser le nom canonique)")
        canon_col=canon
    elif pres<NSYM:
        verdict="NOT_UNIVERSAL"; reasons.append(f"presente sur {pres}/{NSYM} symboles seulement")
    elif fam in PATH_DEPENDENT or c in PATH_DEPENDENT:
        verdict="DEGRADED_PERIOD"; reasons.append("feature a etat/cumulative : saut mecanique a la bascule de source BTC 2026-01-01 (obv -1 940 682 -> -135 056)")
    elif null_hi.get(c,0)>=1:
        verdict="SUSPECT"; reasons.append(f"taux de nuls >20% hors warm-up sur {null_hi[c]}/{NA} symboles audites")
    else:
        verdict="USABLE"; reasons.append("causale par construction (fenetres roulantes), non degeneree, universelle")
    canon_col = dup_of[c].most_common(1)[0][0] if c in dup_of else ""
    rows.append(dict(column=c, family=fam, present_in=f"{pres}/{NSYM}", verdict=verdict, canonical=canon_col, reason=" ; ".join(reasons)))

with open(OUTD+"/evidence/COLUMN_VERDICTS.csv","w",newline="") as fh:
    w=csv.DictWriter(fh,fieldnames=["column","family","present_in","verdict","canonical","reason"]); w.writeheader(); w.writerows(rows)
cnt=collections.Counter(r["verdict"] for r in rows)
print("total colonnes (union):",len(rows))
for k,v in cnt.most_common(): print(f"  {k:16s} {v:5d}  ({100*v/len(rows):.1f}%)")
usable=[r["column"] for r in rows if r["verdict"] in ("USABLE","REDUNDANT_ALIAS")]
json.dump(usable, open(SC+"/usable_cols.json","w"))
print("\nUSABLE ecrit:",len(usable))
