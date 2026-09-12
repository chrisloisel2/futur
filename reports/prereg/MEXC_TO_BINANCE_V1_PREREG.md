# MEXC_TO_BINANCE_V1 — preregistration (DRAFT until sealed; no price read)

> **Status: draft.** It becomes a preregistration only when, in this order: (1) PR #10 is merged so that `LOOK_LEDGER` carries
> regard seq 9 (hash `033b2ab6…`), `LOOP_STATE` reads budget **0**, and the kernel ledger carries family `news` with its five
> trials and its contamination entry; (2) the user has recorded, in an ordinary pushed commit, the two one-off decisions that
> no repository rule grants on its own (§9): a credit of one test **outside the episode rule** and, for a historical look, an
> **override of the `news` contamination burn**; (3) `population.json` is rebuilt from the pinned inputs and pinned, the
> harness sha is written into §10; (4) this file is sealed as a single-file orphan commit on `prereg/mexc-to-binance-v1` and a
> `seal` entry is written to the LOOK_LEDGER before the look.
> **Expected outcome at declared power: INDECIDABLE** (§7). Cost of the attempt: one trial, and the bar of the five earlier
> `news` hypotheses rises from 2.3263 to 2.3940. Accepted in advance by the user's decision recorded in §9.
> Nothing in this file comes from a return after t0. Every number below is pre‑t0 data or a cost measurement.

## 1. The claim, in one sentence

On Binance USDⓈ‑M perpetual launches whose asset was **first listed on MEXC**, the size of the MEXC run‑up over the 24 hours
ending at the last complete hour **before the Binance announcement** orders the **fade** of the Binance perpetual between
t0 + 15 min and t0 + 6 h (short, raw); and in the high‑run‑up group the fade pays a 500 $ taker round trip three times over.

Mechanism (`mexc_to_binance_migration_effect_v1`): MEXC heats up before Binance; Binance opens the perpetual; liquidity and
short access arrive; the move deflates. Nobody is *forced* to trade — a crowd‑positioning story — hence the verdict ceiling
`CANDIDATE_ALPHA_REQUIRES_FORWARD`, never a promotion. **t0** = `tradable_start_ts` of the pinned universe (open of the first
1‑minute Vision bar); it equals the P7 announced opening within 15 min for every kept event (the BAD_TIMESTAMP rule).

## 2. What was already looked at, and what that costs this test

- Regard seq 7 (spot fade, +202 bps, t 3.25, INDECIDABLE: not executable) and regard seq 8 (`event_listing_perp_fade_v1`:
  174 launches pooled, short, **entry t0 + 15 min, exit t0 + 6 h**, +251 bps mean, t 2.12 < 2.326, INDECIDABLE) have been
  read by the author — figures from the published verdicts (`verdict.md`, PROJECT_TRUTH), no result file reopened for this
  draft. The direction "fade" is therefore **informed by data**, not derived from the mechanism alone.
- Every event here is one of the 174 of seq 8; the per‑event table of seq 8 exists on disk. The guarantee that no subgroup
  was informally computed rests on the author's declaration, not on a mechanism. This is a **re‑test on already‑seen
  outcomes** with a new population, a new conditioning variable and a rank statistic. Charged as the **sixth** sealed
  hypothesis of family `news`: `threshold_t(6) = 2.3940` one‑sided, α/6 = 0.00833.
- **Why a rank statistic.** The HIGH‑group mean is not a new statistic: conditional on the pooled +251 bps that seq 8
  revealed, a subgroup mean inherits that level, and a one‑sample t on it would run a conditional type‑I error near 6 %
  instead of 0.8 %. A Spearman correlation between the pre‑announcement run‑up and the signed return, with a permutation
  null over events, conditions on the multiset of outcomes and is invariant to that level. The tradable claim (HIGH mean net
  above the wall) is kept as an **economic gate**, reported with its SE, and carries no significance claim.
- Entry (+15 min) and primary horizon (6 h; hold 5 h 45 as in seq 8's bar rule) are kept **identical to seq 8** to rule out
  horizon shopping; +15 min is also the window the P13 cost decision named (t0 costs 52.9 bps round trip at 500 $).
- **Contamination burn.** The kernel ledger records for family `news`: "regards seq 7 et 8 … 2017‑07‑21 → 2026‑09‑10 : toute
  nouvelle hypothèse news sur cette période est un second regard". The population here (t0 from 2023‑05‑05 to 2026‑09‑06)
  lies entirely inside it. The doctrine says the sealer must refuse. **Absent an explicit user override recorded in §9, this
  preregistration applies forward‑only** (events with t0 after the seal; look when ≥ 60 eligible events exist or on
  2028‑09‑12, whichever first). With the override, the harness records `burn_override: true` in the LOOK_LEDGER entry.

### Deviations from the committed mechanism definition (`research_kernel/mechanisms/mexc_to_binance_migration_effect.py`)

| module says | this prereg does | why |
|---|---|---|
| `REQUIRED_DATA`: account_actual fees | official VIP0 fee at the spot‑confirmed tier (P13 decision) | Binance has no futures‑read‑only key; residual 0.5 bps |
| reference price: MEXC at the same minute | raw Binance perp return, no benchmark | a same‑asset reference measures the basis, not the fade; raw is what a short realises |
| `top1_share_max` 0.10 | 0.20 | at n ≈ 30 the chance top‑1 share under a Gaussian null is ≈ 0.12 median; 0.10 fails most of the time with no effect |
| `capacity_ok_share_min` 0.80 | dropped | book resolution is 100 bps for 65 of the events (status UNKNOWN by construction); the measured cost chain at +15 min is what that criterion was meant to guard |
| placebo: shifted −48 h | post‑listing drift t0 + 7 d (veto‑eligible) and + 30 d (reported) | the perpetual does not exist 48 h before t0 |
| placebo: conditioning permuted across events | **is the primary test** (§5) | — |
| verdict words | REJECTED / INDECIDABLE / CANDIDATE_ALPHA_REQUIRES_FORWARD | user's vocabulary |
| MEXC market | spot and perp first‑markets pooled, split reported | the "no short path" premise holds for the spot half only (40 spot / 44 perp) |

## 3. Population (pre‑t0 data and cost measurements only)

Funnel computed by `first_look.py --build-population` from the pinned files, in this order, no step optional:

| step | rule | n |
|---|---|---|
| 0 | `H2_CAUSAL_POPULATION_MATRIX.population == MEXC_FIRST` | 113 |
| 1 | exclude `class == BAD_TIMESTAMP` (P7 announced opening vs first bar > 15 min; re‑entry only with a documented manual proof, none exists) | 107 |
| 2 | `pre_announcement_return_24h` defined: ≥ 20 complete MEXC hourly closes in the 24 h ending at the last complete hour before the announcement | 84 |
| 3 | effective spread measured at +15 min (`H2_DEPTH_CAPACITY_FEATURES`); when the 500 $ slippage is missing because no book snapshot exists in the window (a Vision gap, 5 events), it is imputed at the population median (3.6 bps round trip) and flagged | **84** |

The step‑3 imputation is a data‑dependent choice declared here; a sensitivity excludes the 5 imputed events. Slippage is a
linear estimate within a 100 bps book band for most events (a lower bound at that resolution); the band upper bound is 200 bps
round trip and is carried in the capacity file. Launch days: 84 distinct (one event per day: cluster‑robust SE = iid SE).
First market on MEXC: spot 40, perp 44 (reported split). Wash covariates: 10 of 84 flagged (robustness only).

## 4. Conditioning variable and groups (fixed before any look)

- Variable: **`pre_announcement_return_24h`** = MEXC close of the last complete hour ≤ `floor_hour(publication_ts)` over the
  MEXC close 24 h earlier, from the close‑bounded hourly tape (P12 fix), ≥ 20 complete closes required. It is strictly
  **before the announcement**, hence before t0; the hours between announcement and t0 (median 1.7 h) are reaction, not
  conditioning, and are dropped (counted per event). Pinned in `MEXC_PRE_ANNOUNCEMENT_RETURN_24H.json`.
- Distribution over the 84 (pre‑t0): median **+5.6 %**, p25 −3.5 %, p75 +31.3 %, min −43 %, max +300 %.
- Pre‑t0 checks: Spearman(variable, per‑event cost) = **−0.27** (a larger run‑up goes with a slightly cheaper book at +15 min:
  the split is partly a liquidity split, disclosed); Spearman(variable, lead time) = +0.03.
- HIGH = `pre_announcement_return_24h ≥ +0.20` (absolute, "a fifth in a day"), **30 events**; LOW = 54. Both event‑id lists are
  frozen in `population.json` (sha256 in §10). The split serves the economic gate only; the primary uses all 84.
- Forbidden as condition: MEXC volume, `liquidity_proxy`, absolute USD volume (global feature policy); `pump_score` and
  `exhaustion_score` (this draft's own exclusion: the first is 7‑day and moved under the boundary fix, the second is 40 %
  volume). `wash_volume_suspect` / `programme_like`: robustness covariates only.

## 5. Hypothesis, statistic, cost (single, direction fixed)

- Per event: short entered at the open of the first 1‑min bar with `open_time ≥ t0 + 15 min` (tolerance 5 min), exited at the
  open of the first bar with `open_time ≥ t0 + 6 h` (same tolerance); a missing entry **or exit** bar excludes the event and
  is counted. Gross `g_i = −1 × log(exit/entry) × 1e4` bps, **raw** (unhedged, no benchmark: a hedge leg would add its own cost).
- **Primary statistic**: Spearman ρ between `pre_announcement_return_24h` and `g_i` over all measured events, one‑sided ρ > 0
  (larger run‑up → larger short gain). p‑value from **20 000 seeded permutations** (seed 20260912) of the variable across
  events; significance if p ≤ α/6 = 0.00833. Equivalent ρ at n = 84: ≈ 0.26 (50 % power), ≈ 0.36 (80 % power).
- Cost per event `c_i` = 10 bps (2 × 5 bps taker, official VIP0, tier confirmed by the spot account, BNB discount not applied)
  + effective spread at +15 min + sell + buy slippage at 500 $ (P11, Binance Vision; imputed for 5). Mean 26.6 bps (HIGH 22.9),
  p90 44.8. Decision `USE_OFFICIAL_PUBLISHED_VIP0_FUTURES_FEES` (P13).
- **Funding**: a 6 h short crosses a settlement; realised funding received by the short at settlements in (entry, exit],
  from the Vision `fundingRate` archives (P6), enters the per‑event net: `net_i = g_i − c_i + f_i`. Read only at the look;
  missing archive → `f_i = 0`, counted, sensitivity excludes those events.
- **Economic gate** (no significance claim): HIGH mean `net_i` ≥ 3 × mean `c_i` of HIGH (≈ 69 bps), reported with iid SE and
  t on the **gross** series (a t on a net measures the cost constant). Concentration: N_eff ≥ 0.45 × n(HIGH), top‑1 share of
  positive net contributions ≤ 0.20, mean net without the largest event > 0.
- Sensitivities — **at most two families, by the kernel's rule** (descriptive, never selected on): (a) horizon: HIGH gross
  at exit +60 min and +24 h; (b) robustness: HIGH net excluding wash‑flagged events, HIGH net excluding cost‑imputed events.
  The LOW group is reported as the complement of the gate (same events, same statistic), not as a sensitivity. Nothing else
  is computed on these events: no BTC‑excess, no winsorised mean, no spot/perp split of returns, no net‑without‑funding.

## 6. Placebos (gross vs gross; veto needs n ≥ 30, t ≥ 1.0 and ≥ 50 % of HIGH gross)

1. **Post‑listing drift control**: HIGH symbols, same clock time, t0 + 7 days (veto‑eligible) and t0 + 30 days (reported).
   Missing bars excluded and counted. Named honestly: new perps drift for weeks; this is a drift control, not a clean null.
2. **Fake symbol** (veto‑eligible): for each HIGH event, a USDT perpetual with `onboardDate ≤ t0 − 90 d` from the pinned
   exchangeInfo snapshot (2026‑09‑10, local, sha256 in §10), candidates sorted by symbol, index = `int(sha256(event_id), 16)
   mod len`. Survivorship (delisted perps absent from the snapshot) lowers this control's drift and is disclosed; if the
   snapshot is absent at the look the placebo is NOT_COMPUTABLE and the verdict ceiling is INDECIDABLE.
3. **Other‑venue‑first control** (reported only): the OKX/Bybit/KuCoin‑first events with their own first‑venue
   `pre_announcement_return_24h` from the P12 control tapes, same statistic and split.
4. **Lead‑time placebo** (reported only): Spearman(lead time, `g_i`); if it orders the fade as strongly as the run‑up, the
   effect is a lead‑time effect.
5. **Conditioning permutation** is the primary null (§5). **Synthetic positive control** at the design regime (n = 84,
   σ₆ₕ = 1 546 bps, effect at ≈ 80 % power) is run before sealing: `results/positive_control.json`.

Placebo prices are fetched only after the LOOK_LEDGER `confirm` entry and the debit.

## 7. Power (declared, not measured)

σ per event on the 6 h statistic at seq 8: **1 546 bps**. Spearman at n = 84: SE ≈ 0.11; ρ needed 0.26 (50 %) / 0.36 (80 %).
Economic gate at n(HIGH) = 30: SE ≈ 282 bps; a mean net of 69 bps sits far inside noise, and t ≥ 2.394 on the gross mean would
need ≈ 675 bps (50 %) / 913 bps (80 %) — the gate is therefore reported, not tested. Under a true ρ of 0.25 the expected verdict
is INDECIDABLE about half the time; under the null, CANDIDATE occurs with probability ≤ 0.0083 (permutation exact) times the
gate pass rate. The MDE that matters is ρ ≈ 0.36 for 80 % power; if that is judged out of reach, the P10 withdrawal clause
applies and the test should not be bought.

**Synthetic positive control at the design regime** (`results/positive_control.json`, 8 seeds, n = 84, σ₆ₕ = 1 546 bps, injected
fade linear in the variable, ≈ 700 bps mean in HIGH): observed ρ 0.16–0.30, **CANDIDATE in 2 of 8 seeds**, INDECIDABLE in 5,
REJECTED in 1 (ρ̂ < 0 by chance); null: 0 of 8 CANDIDATE. The design recovers the effect's sign but not its significance at
this n. A historical look bought under §9 is therefore expected to end INDECIDABLE unless the true conditional fade is well
above 700 bps; this is stated before, not after.

## 8. Verdict (fixed order, three outcomes)

1. measured events < 30 → `INDECIDABLE`
2. ρ̂ ≤ 0 → `REJECTED` (the pre‑announcement run‑up does not order the fade)
3. permutation p > 0.00833 → `INDECIDABLE`
4. n(HIGH) measured < 15 → `INDECIDABLE`
5. HIGH median gross < 30 bps → `REJECTED`
6. HIGH mean net < 3 × mean cost(HIGH) → `REJECTED` (cost wall)
7. concentration fails (N_eff < 0.45 n, top‑1 > 0.20, mean net without largest ≤ 0) or a veto‑eligible placebo vetoes or is
   not computable → `INDECIDABLE`
8. otherwise → `CANDIDATE_ALPHA_REQUIRES_FORWARD`

No verdict is `deployable`. `capital_deployable` stays false. No bot. A `CANDIDATE_ALPHA_REQUIRES_FORWARD` opens a forward
window: each future MEXC‑first launch must be run through the P7/P11/P12 collectors (announcement body, depth capacity,
MEXC pre‑announcement tape); no live collector exists for that today, and the P4 market‑state tape only records the launch.

## 9. Budget, ledgers, overrides (the user's decisions, recorded before the seal)

- **Budget.** True balance after PR #10: **0**. The rule (`H2_BUDGET_REOPEN_REQUEST.md`) credits only new independent
  episodes and says enrichment credits nothing; P6–P12 added no episode. A test here is therefore a **fiat credit outside the
  rule**, recorded as a BUDGET_LEDGER line `__CREDIT__USER_DECISION_OUTSIDE_EPISODE_RULE` (+1) naming MEXC_TO_BINANCE_V1 and
  this file's seal sha, with `LOOP_STATE.budget_tests_remaining` 0 → 1 and a PROJECT_TRUTH line — by the user, never by
  the harness.
- **Burn.** A historical look requires `reports/prereg/MEXC_TO_BINANCE_V1_OVERRIDES.json` with `burn_override: true`, the
  reason, the date and the user's name, pushed before the seal; the LOOK_LEDGER `confirm` entry then records
  `burn_override: true`. Without it: forward‑only (§2).
- **Family.** After PR #10 the kernel ledger holds the five `news` trials; the harness derives the threshold from
  `MultiplicityLedger().current_threshold('news', extra=1)` and refuses if it differs from 2.3940; at the look it records the
  trial in the kernel ledger next to the LOOK_LEDGER entry (one look, two records).
- **Order enforced by the harness**: pins (this file's block, itself included) → LOOK_LEDGER carries seq 9 → BUDGET_LEDGER
  carries the seq 9 debit and a credit naming this prereg → balance == LOOP_STATE ≥ 1 → burn check → `confirm` entry →
  kernel trial → debit → first price. A result file present → refuse. One look. No second look on this universe at 60 m, 6 h
  or 24 h; the two reported sensitivity families burn those horizons for the family.
- **Relation to P10.** This document supersedes `H2_NO_TEST_DECISION.md` step 3 and `H2_CLEAN_RETEST_PREREG.md`. The freeze
  gate (≥ 80 *clean* events) is **not** met — the pinned matrix still carries `execution_cost_status = unknown` for every
  event because it required account_actual fees; it is replaced by the funnel of §3 under the P13 fee decision. Of the three
  fixes the retest draft demanded: entry from the P7 corroborated time — not applied (t0 = first Vision bar, agreeing within
  15 min for all kept); excess vs the index — not applied (raw primary, no benchmark); actual account fee — replaced
  by the official fee at the confirmed tier.

## 10. Pins

```json pins
{
  "universe": {
    "path": "reports/first_look/event_listing_perp_fade_v1_UNIVERSE.json",
    "sha256": "cbb5d9619a01f05c996556ef54c15383b2fbad46d6b2804bf51ca46194caba24"
  },
  "conditioning": {
    "path": "reports/data_acquisition/MEXC_PRE_ANNOUNCEMENT_RETURN_24H.json",
    "sha256": "7c3e39d0fd0cd51c3cf08e4b1646f2fa88cb2a2bcdea3b22c153b5170ceab9cc"
  },
  "causal_matrix": {
    "path": "reports/data_acquisition/H2_CAUSAL_POPULATION_MATRIX.json",
    "sha256": "df5f88eb0e08e2b84a2f368a6800a8143a923fb32a96e64d084d88bef82cb7d7"
  },
  "capacity": {
    "path": "reports/data_acquisition/H2_DEPTH_CAPACITY_FEATURES.json",
    "sha256": "21f0e88048071300bd1e70144309503f1c97e0cacdef8f46b4389c4d35983086"
  },
  "wash": {
    "path": "reports/wash_volume/MEXC_VOLUME_TRUST.json",
    "sha256": "6eb92351d0b85042e2e097f455b17bea388c7d2372bccd7b372f250244e71189"
  },
  "fee_decision": {
    "path": "reports/execution/FUTURES_FEE_DECISION.json",
    "sha256": "9c2267b47a8bd6c7e54ffdaa41f7a295d75d515f2fec3b0576f2aa89f741abda"
  },
  "population": {
    "path": "mechanisms/mexc_to_binance_migration_v1/population.json",
    "sha256": "7a1f29ce282ade06878c182a6bd89d704b1cf5fbdd857a828e2d80176ed90a60"
  },
  "harness": {
    "path": "mechanisms/mexc_to_binance_migration_v1/first_look.py",
    "sha256": "5fec0f8b7c98ae77a80b0d897ef3a068559fe6ff7c353e7b7407c47cc5810522"
  },
  "harness_seq8": {
    "path": "mechanisms/event_listing_perp_fade_v1/first_look.py",
    "sha256": "d0ecbdbaf4f5354adf91fbb684595d617feb4b30f801c6d95fabc5b7451fbbc9"
  },
  "harness_seq7": {
    "path": "mechanisms/event_reaction_v1/first_look.py",
    "sha256": "c6ad33d3c41f20b703f2f7a39e0280e5dc833c56f63509ce16119cecc363b06d"
  },
  "look_ledger_tool": {
    "path": "tools/look_ledger.py",
    "sha256": "c9bb473326a812f27fb53f4b261c565a1fa4df1ad6085c165d87d01a5d058b14"
  },
  "multiplicity": {
    "path": "research_kernel/multiplicity.py",
    "sha256": "31ff2ebf3b01230afb17479324537dc1c634e54e5493951efeeaa50cf88904cf"
  },
  "pre_binance_features": {
    "path": "data_lake/indices/pre_binance_features.py",
    "sha256": "0e525fc9418f29298d08ebcdc91f6071539812b8af85b1f9a41316057b46316f"
  },
  "exchange_info_snapshot": {
    "path": "data_lake/first_look/event_listing_perp_fade_v1/exchangeInfo.json",
    "optional": true,
    "note": "local, gitignored by policy; absent at the look => fake-symbol placebo NOT_COMPUTABLE => verdict ceiling INDECIDABLE",
    "sha256": "9eb76c5d7da7bb26a9201435adbe1bdc6dc4e1bc9ed6d8d766617c746b87fcc7"
  }
}
```

## 11. What is forbidden regardless

Testing continuation; testing a second variable; testing a second horizon as primary; moving the entry, the threshold or the
funnel after a look; choosing the population or the split after seeing returns; re‑reading seq 8; using any volume feature as
a filter; promoting anything from a first look; opening a position.
