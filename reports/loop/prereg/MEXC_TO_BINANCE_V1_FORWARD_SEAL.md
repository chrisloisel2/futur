# MEXC_TO_BINANCE_V1 — FORWARD SEAL (preregistration, forward‑only)

```text
MEXC_TO_BINANCE_V1 is sealed forward-only.
Historical period 2017-07-21 -> 2026-09-10 is burned (kernel ledger, family news).
No historical read is authorized. No burn override. No fiat budget credit.
First eligible event must occur after the seal timestamp (LOOK_LEDGER `seal` entry, branch prereg/mexc-to-binance-v1-forward).
Look condition: n_eligible >= 60 or date >= 2028-09-12 — one look, never before.
Maximum verdict before the forward look: SEALED_NOT_TESTED.
Verdicts at the look: REJECTED / INDECIDABLE / CANDIDATE_ALPHA_REQUIRES_FORWARD. Never deployable.
capital_deployable = false. budget = 0 today; the look debits 1 test credited by the episode rule only.
```

*Sealed as a single‑file orphan commit pushed alone (`tools/look_ledger.py --seal-orphan`), before any forward event exists.
The seal timestamp attested by the remote is the cutoff: every event of the population has t0 strictly after it.*

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
  nouvelle hypothèse news sur cette période est un second regard". The doctrine says the sealer must refuse, and this
  preregistration does: **no historical read is authorized, no override exists in the harness, and every event of the
  forward population must have t0 after the seal timestamp** (hence after the burn).

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

## 3. Population rule (pre‑t0 data and cost measurements only) — and its dry run on history

The funnel below is the **rule**. Applied to the historical files pinned as references it gives the numbers shown — a dry run of the
rule, computed without any return and **never to be looked at**. At the look it is applied, unchanged, to the forward
registry (`reports/forward/mexc_to_binance_v1/`) restricted to events with t0 after the seal:

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
- Historical dry run (pre‑t0, reference only): median **+5.6 %**, p25 −3.5 %, p75 +31.3 %, min −43 %, max +300 %.
- Pre‑t0 checks: Spearman(variable, per‑event cost) = **−0.27** (a larger run‑up goes with a slightly cheaper book at +15 min:
  the split is partly a liquidity split, disclosed); Spearman(variable, lead time) = +0.03.
- HIGH = `pre_announcement_return_24h ≥ +0.20` (absolute, "a fifth in a day"); historical dry run: 30 HIGH / 54 LOW (lists frozen in
  `population.json`, reference only). At the look the HIGH/LOW lists of the forward population are written to
  `results/population_at_look.json` and their sha256 into the LOOK_LEDGER entry. The split serves the economic gate only.
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

## 7. Power (declared, not measured) — and why the historical look was not bought

σ per event on the 6 h statistic at seq 8: **1 546 bps**. Spearman at n = 84: SE ≈ 0.11; ρ needed 0.26 (50 %) / 0.36 (80 %).
Economic gate at n(HIGH) = 30: SE ≈ 282 bps; a mean net of 69 bps sits far inside noise, and t ≥ 2.394 on the gross mean would
need ≈ 675 bps (50 %) / 913 bps (80 %) — the gate is therefore reported, not tested. Under a true ρ of 0.25 the expected verdict
is INDECIDABLE about half the time; under the null, CANDIDATE occurs with probability ≤ 0.0083 (permutation exact) times the
gate pass rate. The MDE that matters is ρ ≈ 0.36 for 80 % power; if that is judged out of reach, the P10 withdrawal clause
applies and the test should not be bought.

**Synthetic positive control at the design regime** (`results/positive_control.json`, 8 seeds, n = 84, σ₆ₕ = 1 546 bps, injected
fade linear in the variable, ≈ 700 bps mean in HIGH): observed ρ 0.16–0.30, **CANDIDATE in 2 of 8 seeds**, INDECIDABLE in 5,
REJECTED in 1 (ρ̂ < 0 by chance); null: 0 of 8 CANDIDATE. The design recovers the effect's sign but not its significance at
this n. A historical look would therefore have ended INDECIDABLE unless the true conditional fade were well above 700 bps;
the same power statement holds for the forward look at n = 60–84 and is stated before, not after.

**Why forward‑only.** Three defects of a historical look, each sufficient: (1) under‑powered against any realistic effect
(above); (2) methodologically burned (§2); (3) less promising after the pre‑announcement correction (+19 % → +5.6 %). The
user refused both a fiat budget credit and a burn override on 2026‑09‑13. No historical read is authorized.

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

## 9. Budget, ledgers, order of operations (forward‑only)

- **Budget.** 0 at the seal (regard seq 9 debited the last test). The look debits **1** test that must have been credited by
  the episode rule (`floor(new independent episodes / 400)`, capped per source) — never by a user decision outside the
  rule; the harness refuses if any `__CREDIT__USER_DECISION_OUTSIDE_EPISODE_RULE` line exists in the BUDGET_LEDGER.
- **Burn.** No override exists. The harness refuses any event whose t0 ≤ 2026‑09‑10 or ≤ the seal timestamp, and any
  window the kernel ledger reports as burned for family `news`.
- **Family.** Sixth sealed `news` hypothesis: the harness derives the threshold from
  `MultiplicityLedger().current_threshold('news', extra=1)` and refuses if it differs from 2.3940; at the look it records the
  trial in the kernel ledger next to the LOOK_LEDGER `confirm` entry (one look, two records).
- **Forward collection.** `forward_collect.py` (pinned) assembles, for every Binance USDⓈ‑M perpetual launched after the seal
  and only those: announcement (P2 tape, P7 body), t0 (first Vision 1‑min bar; provisional = onboardDate until Vision
  publishes), venue precedence (P9), MEXC hourly tape and `pre_announcement_return_24h` (P12, close‑bounded, cut at the
  announcement), wash covariates (P12), capacity at +15 min (P6/P11). It writes the five forward inputs under
  `reports/forward/mexc_to_binance_v1/` and never reads a return. `first_look.py --status` reports `SEALED_NOT_TESTED` and
  the eligible count.
- **Order enforced by the harness at the look**: pins (this file's block, itself included) → LOOK_LEDGER carries seq 9 and
  the `seal` entry of this branch → every event after the seal and outside the burn → budget balance == LOOP_STATE ≥ 1 by
  the episode rule → threshold from the kernel → `confirm` entry (with the sha256 of each forward input and of the HIGH/LOW
  lists) → kernel trial → debit → first price. A result file present → refuse. One look. No second look on these events at
  60 m, 6 h or 24 h.
- **Relation to P10.** Supersedes `H2_NO_TEST_DECISION.md` step 3 and `H2_CLEAN_RETEST_PREREG.md` for the MEXC_FIRST
  population: the historical retest they contemplated is not bought; the population rule, the fee decision (official VIP0 at
  the spot‑confirmed tier) and the measured cost chain replace their preconditions for the forward window only.

## 10. Pins

```json pins
{
  "harness": {
    "path": "mechanisms/mexc_to_binance_migration_v1/first_look.py",
    "role": "code",
    "sha256": "075ffe9d6b885bdcadcf83fe0e8251d231660d37b41a9e5121f5e75c713b6f57"
  },
  "forward_collector": {
    "path": "mechanisms/mexc_to_binance_migration_v1/forward_collect.py",
    "role": "code",
    "sha256": "0e6e35ab894da2c32e2165631c634f7dde5d77ebd41b83239f3a728035ed9f2d"
  },
  "spec": {
    "path": "mechanisms/mexc_to_binance_migration_v1/spec.json",
    "role": "rules",
    "sha256": "6d42ea02b5147627f7597092fbab0fa1140a9f7a8c052bc92ab2cf79f17cf7c1"
  },
  "harness_seq8": {
    "path": "mechanisms/event_listing_perp_fade_v1/first_look.py",
    "role": "code",
    "sha256": "d0ecbdbaf4f5354adf91fbb684595d617feb4b30f801c6d95fabc5b7451fbbc9"
  },
  "harness_seq7": {
    "path": "mechanisms/event_reaction_v1/first_look.py",
    "role": "code",
    "sha256": "c6ad33d3c41f20b703f2f7a39e0280e5dc833c56f63509ce16119cecc363b06d"
  },
  "look_ledger_tool": {
    "path": "tools/look_ledger.py",
    "role": "code",
    "sha256": "c9bb473326a812f27fb53f4b261c565a1fa4df1ad6085c165d87d01a5d058b14"
  },
  "multiplicity": {
    "path": "research_kernel/multiplicity.py",
    "role": "code",
    "sha256": "31ff2ebf3b01230afb17479324537dc1c634e54e5493951efeeaa50cf88904cf"
  },
  "pre_binance_features": {
    "path": "data_lake/indices/pre_binance_features.py",
    "role": "code",
    "sha256": "0e525fc9418f29298d08ebcdc91f6071539812b8af85b1f9a41316057b46316f"
  },
  "mexc_volume_trust": {
    "path": "data_lake/indices/mexc_volume_trust.py",
    "role": "code",
    "sha256": "3aedf13b466a25c3114f63385fdc5e52bd7a99068b217532b84f4adadedf25d7"
  },
  "depth_capacity_features": {
    "path": "data_lake/indices/depth_capacity_features.py",
    "role": "code",
    "sha256": "7641f3fed0aed08817e153e9acaaef70930e0efa19bcf4843394b85627f9a167"
  },
  "venue_precedence": {
    "path": "data_lake/collectors/venue_precedence.py",
    "role": "code",
    "sha256": "c47a4f6eb17c706defc7fe8c781ede7e9c588901b179454ec2a0191544d1d0d9"
  },
  "mexc_pre_binance_tape": {
    "path": "data_lake/collectors/mexc_pre_binance_tape.py",
    "role": "code",
    "sha256": "e6b124dd1073bd0756a0d29b6fa33367b7eeb0eab6d8b64293e107f93e5cf466"
  },
  "fee_decision": {
    "path": "reports/execution/FUTURES_FEE_DECISION.json",
    "role": "rules",
    "sha256": "9c2267b47a8bd6c7e54ffdaa41f7a295d75d515f2fec3b0576f2aa89f741abda"
  },
  "historical_universe": {
    "path": "reports/first_look/event_listing_perp_fade_v1_UNIVERSE.json",
    "role": "historical_reference_never_looked_at",
    "sha256": "cbb5d9619a01f05c996556ef54c15383b2fbad46d6b2804bf51ca46194caba24"
  },
  "historical_conditioning": {
    "path": "reports/data_acquisition/MEXC_PRE_ANNOUNCEMENT_RETURN_24H.json",
    "role": "historical_reference_never_looked_at",
    "sha256": "7c3e39d0fd0cd51c3cf08e4b1646f2fa88cb2a2bcdea3b22c153b5170ceab9cc"
  },
  "historical_causal_matrix": {
    "path": "reports/data_acquisition/H2_CAUSAL_POPULATION_MATRIX.json",
    "role": "historical_reference_never_looked_at",
    "sha256": "df5f88eb0e08e2b84a2f368a6800a8143a923fb32a96e64d084d88bef82cb7d7"
  },
  "historical_capacity": {
    "path": "reports/data_acquisition/H2_DEPTH_CAPACITY_FEATURES.json",
    "role": "historical_reference_never_looked_at",
    "sha256": "21f0e88048071300bd1e70144309503f1c97e0cacdef8f46b4389c4d35983086"
  },
  "historical_wash": {
    "path": "reports/wash_volume/MEXC_VOLUME_TRUST.json",
    "role": "historical_reference_never_looked_at",
    "sha256": "6eb92351d0b85042e2e097f455b17bea388c7d2372bccd7b372f250244e71189"
  },
  "population_dry_run": {
    "path": "mechanisms/mexc_to_binance_migration_v1/population.json",
    "role": "historical_reference_never_looked_at",
    "sha256": "7a1f29ce282ade06878c182a6bd89d704b1cf5fbdd857a828e2d80176ed90a60"
  },
  "exchange_info_snapshot": {
    "path": "data_lake/first_look/event_listing_perp_fade_v1/exchangeInfo.json",
    "role": "historical_reference_never_looked_at",
    "optional": true,
    "note": "local snapshot for the fake-symbol draw; absent at the look => placebo NOT_COMPUTABLE => ceiling INDECIDABLE",
    "sha256": "9eb76c5d7da7bb26a9201435adbe1bdc6dc4e1bc9ed6d8d766617c746b87fcc7"
  },
  "forward_universe": {
    "path": "reports/forward/mexc_to_binance_v1/UNIVERSE.json",
    "role": "forward_input",
    "note": "grows with launches; sha256 recorded in the LOOK_LEDGER entry at the look"
  },
  "forward_conditioning": {
    "path": "reports/forward/mexc_to_binance_v1/MEXC_PRE_ANNOUNCEMENT_RETURN_24H.json",
    "role": "forward_input"
  },
  "forward_causal_matrix": {
    "path": "reports/forward/mexc_to_binance_v1/CAUSAL_MATRIX.json",
    "role": "forward_input"
  },
  "forward_capacity": {
    "path": "reports/forward/mexc_to_binance_v1/CAPACITY_FEATURES.json",
    "role": "forward_input"
  },
  "forward_wash": {
    "path": "reports/forward/mexc_to_binance_v1/WASH.json",
    "role": "forward_input"
  }
}
```

## 11. What is forbidden regardless

Testing continuation; testing a second variable; testing a second horizon as primary; moving the entry, the threshold or the
funnel after a look; choosing the population or the split after seeing returns; re‑reading seq 8; using any volume feature as
a filter; promoting anything from a first look; opening a position.
