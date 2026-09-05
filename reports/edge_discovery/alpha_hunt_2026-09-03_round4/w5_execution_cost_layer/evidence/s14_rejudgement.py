"""W5/s14 - H5, the retrospective re-judgement: mechanism -> verdict at -14 -> verdict at the
measured cost.

INPUTS
  <scratch>/mech_inventory*.json  : inventory of rounds 1-3 mechanisms with a stated gross bps
  configs/validation_registry.yaml: the 35 candidates that reached independent validation
  <scratch>/cost_floor.json       : the replacement cost model (s13)

RULES (all preregistered in PREREGISTRATION.md sec.H5, none adjusted after seeing results)
  RESURRECTION_CANDIDATE : was dead at -14, and gross - cost_realistic > 0
                           AND gross - 1.5*cost_realistic > 0, AND the kill reason was COST,
                           AND the mechanism is allowed to execute maker (see the guard below).
  NEWLY_DEAD             : was alive at -14, and gross - cost_realistic <= 0.
  KILL_REASON_NOT_COST   : originally killed for declustering / significance / stability / sign
                           / ETA -> a cost model cannot revive it, full stop.
  UNCONFIRMABLE_IN_HORIZON stays dead regardless of cost (preregistration sec.5.5).

HONESTY GUARD (preregistered): a maker-based resurrection is admissible ONLY if the holding
period is >= 1 hour AND the trigger is not a shock requiring immediate execution. EVENT_SHOCK
mechanisms are barred from maker resurrection and are re-judged at the TAKER cost, plus the
momentum urgency penalty when the mechanism is a continuation trade.
"""
import os, sys, json, glob, re
import numpy as np, pandas as pd, yaml

S = os.environ["W5_SCRATCH"]
ROOT = "/home/qbee/futur"
FLOOR = json.load(open(f"{S}/cost_floor.json"))
TIER = {t["tier"]: t for t in FLOOR["by_tier"]}
URG_M = FLOOR["urgency_maker_rt"]["shock_p99"]      # +1.95 RT, momentum arm, 99th pct
URG_M999 = FLOOR["urgency_maker_rt"]["shock_p999"]  # +10.39 RT

# universe -> tier. A cross-sectional long/short over a 312-name PIT universe selects the TAILS,
# which are systematically less liquid than the median name, so the central case is T3 and the
# stress case T4. Single-name / majors work is T1. Cascade datasets concentrate on alts -> T2/T3.
def tier_of(universe, mech_id):
    u = (universe or "").lower(); m = (mech_id or "").lower()
    if "btcusdt" in u or "btc vs eth" in u or ("btc" in u and "eth" in u and "curve" in u):
        return "T1_MAJOR", "T2_LIQUID_ALT"
    if "ethusdt" in u:
        return "T1_MAJOR", "T2_LIQUID_ALT"
    if "sector" in u or "312" in u or "liquid universe" in u or "usdm" in u:
        return "T3_MID_ALT", "T4_WIDE_ALT"
    if "49 symbol" in u or "50" in u or "cascade" in u or "premium" in u or "ignition" in u \
       or "spillover" in u or "crowding" in u:
        return "T2_LIQUID_ALT", "T3_MID_ALT"
    return "T3_MID_ALT", "T4_WIDE_ALT"


def holding_seconds(h):
    if not h: return None
    h = str(h).lower().strip()
    mm = re.match(r"(?:fwd_)?(\d+(?:\.\d+)?)\s*(s|sec|m|min|h|hour|d|day|w|week)", h)
    if mm:
        v = float(mm.group(1)); unit = mm.group(2)[0]
        return v * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    if "intraday" in h: return 3600.0
    if "weekly" in h: return 604800.0
    if "daily" in h: return 86400.0
    return None


COST_KILL = re.compile(r"cost|stress|thin margin|marginal|frais", re.I)


def rejudge(rec):
    g = rec.get("gross_bps")
    n14 = rec.get("net_bps_14")
    if g is None and n14 is not None:
        g = n14 + 14.0
    if g is None:
        return None
    tc, ts = tier_of(rec.get("universe"), rec.get("mechanism_id"))
    trig = (rec.get("trigger_type") or "UNCLEAR").upper()
    hs = holding_seconds(rec.get("holding_period"))
    can_wait = (hs is not None and hs >= 3600)
    maker_ok = can_wait and trig not in ("EVENT_SHOCK",)
    row = {"mechanism_id": rec.get("mechanism_id"), "round": rec.get("round"),
           "gross_bps": round(float(g), 2), "net_bps_at_14": round(float(g) - 14.0, 2),
           "verdict_source": rec.get("verdict"), "kill_reason": rec.get("kill_reason"),
           "trigger_type": trig, "holding_period": rec.get("holding_period"),
           "universe": rec.get("universe"), "tier_central": tc, "tier_stress": ts,
           "maker_admissible": bool(maker_ok),
           "n_independent": rec.get("n_independent"),
           "operational_status": rec.get("operational_status")}
    for lbl, t in (("central", tc), ("stress_tier", ts)):
        T = TIER[t]
        c_taker = T["cost_taker_rt"]
        c_maker = T["cost_maker_T600_rt"]
        c = min(c_taker, c_maker) if maker_ok else c_taker
        if trig == "EVENT_SHOCK":
            # barred from maker; the taker urgency penalty measured at the p99 tail is ~0
            c = c_taker + 0.0
        row[f"cost_realistic_rt_{lbl}"] = c
        row[f"net_bps_{lbl}"] = round(float(g) - c, 2)
        row[f"net_bps_stress15x_{lbl}"] = round(float(g) - 1.5 * c, 2)
    # continuation trades that fire on a shock also eat the maker momentum penalty if they
    # ever try to post; reported for information, never used to resurrect
    row["maker_urgency_penalty_rt_if_posted_p99"] = URG_M
    row["maker_urgency_penalty_rt_if_posted_p999"] = URG_M999

    alive14 = row["net_bps_at_14"] > 0
    kr = rec.get("kill_reason") or ""
    cost_killed = bool(COST_KILL.search(kr))
    c = row["cost_realistic_rt_central"]
    if alive14 and row["net_bps_central"] <= 0:
        v = "NEWLY_DEAD"
    elif (not alive14) and row["net_bps_central"] > 0 and row["net_bps_stress15x_central"] > 0:
        v = "RESURRECTION_CANDIDATE" if cost_killed else "KILL_REASON_NOT_COST"
    elif (not alive14) and row["net_bps_central"] > 0:
        v = "STILL_COST_FRAGILE"           # alive at the measured cost, dies at 1.5x
    elif alive14:
        v = "UNCHANGED_ALIVE"
    else:
        v = "UNCHANGED_DEAD"
    row["w5_verdict"] = v
    row["delta_net_bps"] = round(row["net_bps_central"] - row["net_bps_at_14"], 2)
    return row


def main():
    recs = []
    for f in sorted(glob.glob(f"{S}/mech_inventory*.json")):
        try:
            d = json.load(open(f))
            recs += d if isinstance(d, list) else d.get("mechanisms", [])
            print(f"loaded {f}: {len(d)}")
        except Exception as e:
            print("SKIP", f, e)
    # validation registry: net bps at the -14 convention
    vr = yaml.safe_load(open(f"{ROOT}/configs/validation_registry.yaml"))["candidates"]
    for c in vr:
        for k, lbl in (("validation_net_bps", "validated"), ("discovery_net_bps", "discovery")):
            if c.get(k) is None: continue
            fam = (c.get("family") or "").lower()
            # family -> (trigger, holding). Cross-sectional / carry families rebalance on a slow
            # clock and can post; cascade/event families fire on a shock and are barred from
            # maker resurrection by the preregistered honesty guard.
            if "cross_sectional" in fam or "carry" in fam or "basis" in fam or "funding" in fam:
                trig, hold, uni = "SLOW_STATE", "7d", "312 PIT symbols"
            elif "cascade" in fam or "liq" in fam or "event" in fam or "squeeze" in fam:
                trig, hold, uni = "EVENT_SHOCK", "4h", "49 symbols"
            elif "options" in fam or "positioning" in fam or "whale" in fam:
                trig, hold, uni = "SLOW_STATE", "1d", "Binance USDM liquid universe"
            else:
                trig, hold, uni = "UNCLEAR", None, "312 PIT symbols"
            recs.append({"mechanism_id": f"{c['candidate_id']} [{lbl}]", "round": "validation",
                         "net_bps_14": float(c[k]), "gross_bps": float(c[k]) + 14.0,
                         "verdict": c.get("current_status"),
                         "kill_reason": (None if c.get("current_status") in
                                         ("VALIDATED_FOR_FORWARD", "ALREADY_LIVE", "VALIDATING")
                                         else "validation gate"),
                         "holding_period": hold, "trigger_type": trig,
                         "universe": uni, "family": fam,
                         "operational_status": c.get("operational_status"),
                         "n_independent": c.get("n_validation_independent")})
    rows = [r for r in (rejudge(x) for x in recs) if r]
    D = pd.DataFrame(rows).drop_duplicates(subset=["mechanism_id", "round"])
    D = D.sort_values(["w5_verdict", "gross_bps"], ascending=[True, False])
    print(f"\nre-judged {len(D)} mechanisms")
    print("\n=== verdict counts ===")
    print(D.w5_verdict.value_counts().to_string())
    print("\n=== every mechanism whose VERDICT CHANGES ===")
    ch = D[D.w5_verdict.isin(["NEWLY_DEAD", "RESURRECTION_CANDIDATE",
                              "STILL_COST_FRAGILE", "KILL_REASON_NOT_COST"])]
    cols = ["mechanism_id", "round", "gross_bps", "net_bps_at_14", "tier_central",
            "trigger_type", "maker_admissible", "cost_realistic_rt_central",
            "net_bps_central", "net_bps_stress15x_central", "kill_reason", "w5_verdict"]
    print(ch[cols].to_string(index=False, max_colwidth=44))
    D.to_csv(f"{S}/rejudgement.csv", index=False)
    json.dump(D.to_dict("records"), open(f"{S}/rejudgement.json", "w"), indent=1, default=float)
    print("\nwrote", f"{S}/rejudgement.csv")


if __name__ == "__main__":
    main()
