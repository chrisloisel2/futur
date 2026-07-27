# V2 Source Review — `01-Audit.txt` / `02-Etat-de-l-art.txt`

## Status: still BLOCKED for the two named files — re-verified 2026-07-27 (session 3)

Asked again this session to verify SHA-256 hashes and classify claims from:

| File | Claimed SHA-256 |
|---|---|
| `project_sources/01-Audit.txt` | `516cee6261e15c8d30d963940d079e968c3ea2149a2304864250c50799bd2b5c` |
| `project_sources/02-Etat-de-l-art.txt` | `9fbe135e48afd0ec3b287735098b1890e254d7d752e0c366ad2db6cc8919e00e` |

Re-ran the same search as the prior two sessions — `project_sources/` at repo
root, Spotlight (`mdfind -name`) for both exact filenames, a full
home-directory `find` (excluding `~/Library`, `~/.Trash`), and the other
working directories available this session. **Zero hits, identical result to
both prior sessions.** No SHA-256 can be computed against a file that
doesn't exist — `shasum -a 256` was not run against anything, because
running it against the wrong file (or a guess) and reporting a match/mismatch
would itself be fabricated evidence. This is not resolved by trying harder
with the same search; it needs the actual file, from you.

**Do not re-ask me to re-search a fourth time without a new location** — at
that point the answer will not change. Either attach/paste the files, or
tell me a path on this machine I haven't checked.

## What this session did instead: independent repo-internal evidence

The two specific claims named in this session's instructions (Commit 1
steps 3–4) turn out to be independently verifiable from files **already
committed in this repo**, without needing the two missing documents. These
are classified below on that basis — sourced to real repo paths, not to
`01-Audit.txt`/`02-Etat-de-l-art.txt`, which I have still never read. If the
missing documents say something different about these same topics, that
would only be discoverable once they're actually provided.

### Claim: "seven-layer architecture" (`architecture sept niveaux`)

**Classification: HISTORICAL_ONLY / SUPERSEDED — CONFIRMED with direct evidence.**

`legacy/audit.md` (dated 2026-04-11, itself an old audit already filed under
`legacy/`) documents a cascaded decision pipeline of exactly 7 levels:

> "Prendre des décisions de trading via pipeline ML hiérarchique 7 niveaux"
> (`legacy/audit.md:33`) — `ai/models/level_0/` (Global Gating, NumPy) through
> `level_7/` (Risk Controller, NumPy), with TensorFlow levels 1-6 in between
> (`legacy/audit.md:67-75`).

That same document already flagged it as non-functional at the time: *"Level
0 → mock, Level 1 → mock, Level 2 → mock, Level 3 → mock, Level 4 → mock,
Level 5 → mock, Level 6, 7 → absents"* (`legacy/audit.md:923-929`); *"Le code
Level 7 est complet mais n'est instancié nulle part dans les pipelines
d'exécution"* (`:971`); *"Absence de backtest end-to-end Level 0-7"* (`:978`).

Confirmed physically superseded, not just criticized: `ai/models/level_0`
through `level_7` no longer exist in the working tree — they were moved to
`legacy/ai/models/` (only `level_0`, `level_1`, `level_2`, `level_7`
survived the move; `level_3`-`level_6` are gone even from `legacy/`). The
currently-live `ai/level_0/` and `ai/level_2/` directories (git-touched as
recently as 2026-06-28, still imported by `scripts/`, per
`docs/v2/MIGRATION.md`) are a **different, much simpler design** — plain
feature engineering (`ai/level_0/`) and a "TRM Fleet" gradient-boosting
ensemble (`ai/level_2/`), matching the newer `legacy/docs/audit.md`'s
(2026-05-10) table of contents, which describes only "Couche 0" and "Couche
2," no 7-level cascade at all. The 7-level TF/PyTorch architecture was
abandoned before this V2 effort started; it is not part of any current
CANONICAL_CANDIDATE or MIGRATE path in `docs/v2/INVENTORY.generated.md`.

### Claim: old SOL/BNB results, "+0.3–0.5%/month"

**Classification: SUPERSEDED — evidence found, but the exact figure could
not be independently corroborated.**

Two independent pieces of repo evidence confirm SOL/BNB-specific results
from this project were flagged as unreliable and never promoted:

- `legacy/dead_reports/BILAN_AVANCEMENT_MAI_2026.md` (already filed under
  `dead_reports/`) — validation table on 2024 OOS data: `SOLUSDT val_PF=999,
  val_n=2` marked *"⚠ n=2 (non significatif)"* (a 2-trade sample producing a
  PF of 999 is a textbook small-n artifact, not an edge); `BNBUSDT
  val_PF=0.11, val_n=4` marked *"✗ Rejeté val"*. The document's own
  recommendation: *"Désactiver BNB, XRP, AVAX du live (val_PF < 1)"* and
  *"leurs signaux sont loggés mais ne devraient pas être suivis sans
  recalibration."*
- `reports/experiments.yaml` (migrated into `reports/registry/experiments.jsonl`
  this same V2 effort, see `docs/v2/MIGRATION.md`) — every `BTC+ETH+BNB+SOL`
  / `BTC+ETH+SOL+BNB` universe entry has `decision: incubate` or `reject`,
  `pf_oos` between 0.0 and 0.89 (i.e. **losing** on a profit-factor basis
  out of sample), never `promote` or equivalent. None of these entries are
  cited anywhere as validated.

**What I could not confirm:** a literal "+0.3–0.5%/month" figure tied
specifically to SOL/BNB. Grepped `reports/`, `research/`, `legacy/*.md`,
`legacy/docs/*.md` for that range in various formats (`0,3–0,5 %/mois`,
`0.3-0.5%/month`, etc.) — no exact match. The closest adjacent figures in
the repo are unrelated to SOL/BNB specifically: `reports/V1.1_BASELINE.md`'s
BTC/ETH carry+long baseline at +4.8%/year (≈0.4%/month, but that portfolio
is BTC/ETH delta-neutral carry, not SOL/BNB), and
`reports/PORTFOLIO_V1_REPORT.md`'s carry gate threshold of "0.25%/mo" for
BTC. If "+0.3–0.5%/month" is a figure specifically stated in
`01-Audit.txt`/`02-Etat-de-l-art.txt`, I cannot verify or cross-reference it
without those files — the SOL/BNB classification above stands on its own
evidence (small-sample artifact + rejected-in-validation + never-promoted)
regardless of the exact monthly-return number attached to it elsewhere.

## Bottom line

Both requested classifications (seven-layer architecture, old SOL/BNB
results) are recorded above as **SUPERSEDED**, on evidence from files
already in this repo — this does not require or wait on
`01-Audit.txt`/`02-Etat-de-l-art.txt`. Full per-claim review of those two
documents themselves remains blocked until they're actually available.
