#!/usr/bin/env python3
"""
scripts/run_live_alpha_lab_cycle.py
─────────────────────────────────────────────────────────────────────────────
ORCHESTRATEUR DU PAPER TRADING LIVE (Live Alpha Lab).

Pourquoi ce fichier existe
──────────────────────────
Jusqu'ici, les 9 producteurs de signal (`scripts/run_*_shadow.py`) et la
couche portefeuille (`scripts/run_portfolio_shadow.py`) n'étaient lancés QUE
À LA MAIN. Aucune unité systemd ne les couvrait. Conséquence mesurée le
2026-09-03 : la décision forward la plus fraîche du laboratoire datait de 38
heures, et AMIHUD_ILLIQUIDITY_PREMIUM_V1 -- figé et « lancé en forward » la
veille -- n'avait produit aucune décision forward du tout.

Un forward-test qui ne tourne pas n'accumule pas de preuve. C'est le seul
problème que ce script résout : rendre le paper trading CONTINU et
AUTOMATIQUE, sans changer une seule spec d'alpha.

Ce que ce script ne fait PAS
────────────────────────────
- Aucun ordre réel. Tout reste en paper/shadow (mark-to-market interne).
- Aucune modification de spec, de seuil, d'univers ou de ledger existant.
- Aucun recalcul du passé : les producteurs sont append-only et idempotents
  (déduplication sur leur propre clé), la relance est donc sans effet de bord.

Ordre d'exécution (imposé, pas cosmétique)
──────────────────────────────────────────
  0. collecte de la queue fraîche (métriques dérivées 5 m) + sonde de spread
  1. producteurs de signal  (position + gate + overlay)
  2. étiquetage provenance  (REPLAY vs FORWARD_LIVE)
  2b. scellement des résultats (label des décisions dont l'horizon vient d'échoir)
  3. couche portefeuille    (lit le gate WHALE_LSR + l'overlay VOL_FORECAST)
  4. scoreboards            (lisent les ledgers écrits en 1, les labels de 2b
                             et l'état écrit en 3)

L'étape 0 est DANS le cycle et non dans un timer séparé, précisément pour que
l'ordre soit garanti : les producteurs de la famille cascade lisent la série
que cette étape vient d'étendre. Deux timers indépendants se croiseraient et
un producteur pourrait tourner sur une queue vieille d'un cycle. Un échec de
collecte n'interrompt pas le cycle -- les producteurs retombent alors sur la
seule archive Vision, c'est-à-dire l'ancien comportement, dégradé mais correct.

Le gate et l'overlay DOIVENT être frais avant l'agrégation de portefeuille,
sinon le portefeuille filtre sur un screen périmé.

L'étape 2b est ce qui rend le forward-test LISIBLE. Jusqu'ici le lab comptait
862 décisions forward sans savoir ce qu'AUCUNE d'elles avait rapporté (le
scoreboard imprimait littéralement `PENDING_outcome_labeling_not_built`). Elle
est DANS le cycle, et pas dans un batch séparé, pour une raison de fond : un
batch rétrospectif peut être relancé avec d'autres paramètres jusqu'à ce que le
chiffre plaise, alors qu'un label écrit à l'échéance et refusé à la réécriture
ne le peut pas. Le passage toutes les 15 min est ce qui permet à la quasi-
totalité des labels de naître SEALED_AT_MATURITY plutôt que LATE_BACKFILL —
et cette distinction est portée par la donnée elle-même, donc un labelliseur
en panne se dénonce tout seul dans le ledger. Elle vient APRÈS l'étiquetage
de provenance (elle ne labellise que du FORWARD_LIVE) et AVANT le portefeuille
(qui peut échouer sans devoir emporter la preuve avec lui).

L'étape 2 n'est PAS optionnelle — c'est un bug corrigé en câblant ce cycle.
Aucun `run_*_shadow.py` ne pose lui-même la colonne `provenance` : elle n'est
écrite que par scripts/apply_provenance_tags.py, qui n'était appelé à la main
que ponctuellement. Or run_portfolio_shadow.load_forward_only() et les deux
scoreboards filtrent sur `provenance == "FORWARD_LIVE"` et ignorent (fail
closed) toute ligne non étiquetée. En fonctionnement continu sans cette étape,
chaque décision fraîchement produite serait donc restée invisible au
portefeuille et au compteur forward : le paper trading aurait tourné en
n'ouvrant jamais de position sur un signal neuf. Le docstring d'
apply_provenance_tags.py prévoyait explicitement ce rattachement ("sûr à
relancer après chaque run des scripts run_*_shadow.py") ; il manquait
seulement la boucle pour l'appeler.

Robustesse
──────────
- flock : deux cycles ne se chevauchent jamais (les producteurs écrivent les
  mêmes parquets).
- Un producteur qui échoue ou dépasse son timeout n'interrompt pas le cycle —
  il est enregistré en erreur et les autres continuent. Un alpha cassé ne doit
  pas geler tout le laboratoire.
- Garde-fou disque : le disque de la machine est à 97%. En dessous du plancher,
  le cycle s'arrête AVANT d'écrire quoi que ce soit (les collecteurs live sont
  prioritaires sur le forward-test).
- Détection de dérive registre/runners : tout alpha en SIGNAL_SHADOW dans le
  registre sans producteur déclaré est signalé bruyamment. C'est le détecteur
  du cas « alpha validé mais qui ne trade pas ».

Sorties
───────
  reports/live_alpha_lab/CYCLE_STATE.json   dernier cycle (lisible machine)
  reports/live_alpha_lab/cycle_log.jsonl    historique append-only des cycles
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNNERS_CFG = ROOT / "configs" / "live_alpha_runners.yaml"
REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
LAB_DIR = ROOT / "reports" / "live_alpha_lab"
STATE_PATH = LAB_DIR / "CYCLE_STATE.json"
LOG_PATH = LAB_DIR / "cycle_log.jsonl"
LOCK_PATH = LAB_DIR / ".cycle.lock"

COLLECTOR_SCRIPT = "scripts/collect_oi_metrics_5m.py"
SPREAD_PROBE_SCRIPT = "scripts/probe_spread_cross_section.py"
PROVENANCE_SCRIPT = "scripts/apply_provenance_tags.py"
OUTCOME_LABEL_SCRIPT = "scripts/label_forward_outcomes.py"
PORTFOLIO_SCRIPT = "scripts/run_portfolio_shadow.py"
SCOREBOARD_SCRIPTS = [
    "scripts/compute_live_alpha_lab_scoreboard.py",
    "scripts/compute_validation_scoreboard.py",
]

# Plancher d'espace libre machine, DÉLIBÉRÉMENT PLUS BAS que celui des
# collecteurs (scripts/collect_microstructure_reduced.py, 20 GB) : ce cycle
# s'arrête donc APRÈS eux, pas avant.
#
# Ce n'est pas une inversion de priorité, c'est une question de volumes. Les
# collecteurs écrivent ~0,89 Go/jour de données brutes irremplaçables ; ce cycle
# écrit des parquets de décisions de l'ordre du mégaoctet. L'arrêter en premier
# ne libérerait donc rien de mesurable, et coûterait la visibilité (scoreboards,
# état du portefeuille) précisément au moment où le disque se remplit et où on
# en a le plus besoin. Son plancher n'est qu'une auto-protection de dernier
# recours contre le cas où ce serait LUI qui déborde.
MIN_FREE_DISK_GB = 15.0

PYTHON = str(ROOT / ".venv" / "bin" / "python")


def now() -> datetime:
    return datetime.now(timezone.utc)


def free_gb() -> float:
    return shutil.disk_usage(str(ROOT)).free / (1024 ** 3)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            # état corrompu : on repart à vide plutôt que de bloquer le cycle.
            # Perdre l'historique des cadences fait juste retourner les
            # producteurs une fois de trop — inoffensif car idempotents.
            return {}
    return {}


def last_success_map(state: dict) -> dict:
    """alpha_id -> timestamp ISO du DÉBUT DE CYCLE du dernier run réussi.

    Volontairement le début du CYCLE et non celui de l'étape. Mesuré le
    2026-09-05, après l'ajout de la collecte en étape 0 : celle-ci dure ~136 s
    et décale d'autant le démarrage des producteurs, qui retombaient alors
    juste SOUS leur cadence de 15 min au cycle suivant et étaient sautés. Un
    producteur ne tournait donc plus qu'un cycle sur deux, ce qui doublait son
    plancher de latence -- l'inverse exact de ce que la collecte fraîche
    cherchait à obtenir. La cadence exprime « au plus une fois toutes les N
    minutes », une propriété du rythme du cycle, pas de la durée des étapes qui
    le précèdent.

    Porté d'un cycle à l'autre : un producteur en échec ne doit pas voir sa
    cadence réinitialisée (sinon un alpha cassé serait retenté à chaque cycle
    quelle que soit sa cadence, ce qui est exactement le comportement voulu
    pour un événementiel à 15 min mais pas pour un cross-sectionnel à 6h).
    """
    return dict(state.get("last_success", {}))


def is_due(alpha_id: str, cadence_minutes: int, last_success: dict, force: bool) -> bool:
    if force:
        return True
    ts = last_success.get(alpha_id)
    if not ts:
        return True
    try:
        prev = datetime.fromisoformat(ts)
    except ValueError:
        return True
    age_min = (now() - prev).total_seconds() / 60.0
    return age_min >= cadence_minutes


def run_step(name: str, script: str, timeout_sec: int) -> dict:
    """Lance un script en sous-processus isolé.

    Sous-processus et non import : un producteur qui plante (segfault DuckDB,
    OOM, boucle infinie) ne doit pas emporter l'orchestrateur avec lui, et le
    timeout doit être réellement applicable — ce qu'un import ne permet pas.
    """
    t0 = time.time()
    started = now().isoformat()
    try:
        proc = subprocess.run(
            [PYTHON, script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
        status = "OK" if rc == 0 else "FAILED"
    except subprocess.TimeoutExpired as exc:
        rc, status = None, "TIMEOUT"
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = f"timeout after {timeout_sec}s"
    except OSError as exc:
        rc, status, out, err = None, "FAILED", "", f"{type(exc).__name__}: {exc}"

    duration = round(time.time() - t0, 2)
    # On ne garde que la queue des flux : un producteur cross-sectionnel peut
    # cracher des milliers de lignes, et ce log est écrit à chaque cycle.
    tail = lambda s: "\n".join((s or "").strip().splitlines()[-12:])
    record = {
        "name": name,
        "script": script,
        "status": status,
        "returncode": rc,
        "duration_sec": duration,
        "started_at": started,
        "stdout_tail": tail(out),
        "stderr_tail": tail(err),
    }
    marker = {"OK": "✓", "FAILED": "✗", "TIMEOUT": "⏱"}[status]
    print(f"[cycle] {marker} {name:38s} {status:8s} {duration:7.1f}s", flush=True)
    if status != "OK":
        print(f"[cycle]   stderr: {record['stderr_tail'][:500]}", flush=True)
    return record


def registry_drift(runner_ids: set) -> list:
    """Alphas que le registre déclare en shadow mais qu'aucun producteur ne fait
    tourner. C'est le détecteur du cas « validé/figé mais qui ne trade pas » —
    exactement l'écart qui a laissé AMIHUD_ILLIQUIDITY_PREMIUM_V1 sans aucune
    décision forward après son freeze."""
    reg = yaml.safe_load(REGISTRY.read_text())
    missing = []
    for a in reg.get("alphas", []):
        if a.get("operational_status") in ("SIGNAL_SHADOW", "EXECUTION_SHADOW"):
            if a["alpha_id"] not in runner_ids:
                missing.append(a["alpha_id"])
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description="Cycle complet du Live Alpha Lab (paper).")
    ap.add_argument("--force", action="store_true",
                    help="ignore les cadences, relance tous les producteurs")
    ap.add_argument("--only", default=None,
                    help="ne lance qu'un alpha_id (débogage) ; portefeuille et scoreboards suivent quand même")
    ap.add_argument("--skip-portfolio", action="store_true")
    ap.add_argument("--skip-scoreboards", action="store_true")
    args = ap.parse_args()

    LAB_DIR.mkdir(parents=True, exist_ok=True)
    cycle_started = now()

    lock_fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Cas normal, pas une erreur : le cycle précédent dure plus longtemps
        # que la cadence du timer. On sort proprement pour ne pas empiler.
        print("[cycle] un autre cycle est déjà en cours — sortie propre", flush=True)
        return 0

    free = free_gb()
    if free < MIN_FREE_DISK_GB:
        msg = (f"espace disque insuffisant : {free:.1f}GB libres < plancher "
               f"{MIN_FREE_DISK_GB}GB — cycle annulé avant toute écriture")
        print(f"[cycle] ✗ {msg}", flush=True)
        state = {"cycle_started_at": cycle_started.isoformat(), "status": "ABORTED_DISK",
                 "reason": msg, "free_gb": round(free, 2),
                 "last_success": last_success_map(load_state())}
        STATE_PATH.write_text(json.dumps(state, indent=2))
        with LOG_PATH.open("a") as fh:
            fh.write(json.dumps(state) + "\n")
        return 1

    cfg = yaml.safe_load(RUNNERS_CFG.read_text())
    runners = cfg["runners"]
    runner_ids = {r["alpha_id"] for r in runners}

    drift = registry_drift(runner_ids)
    if drift:
        print(f"[cycle] ⚠ DÉRIVE REGISTRE : {len(drift)} alpha(s) en shadow SANS producteur "
              f"déclaré -> {', '.join(drift)}. Ces alphas n'accumulent AUCUNE preuve forward.",
              flush=True)

    prev_state = load_state()
    last_success = last_success_map(prev_state)

    # Étape 0 — queue fraîche. Toujours avant les producteurs (cf. docstring).
    # ~200 appels API pour 50 symboles, une centaine de secondes.
    collector_rec = run_step("COLLECT_OI_METRICS_5M", COLLECTOR_SCRIPT, 900)

    # Sonde de spread — 1 requête REST, ~50 lignes. Le coût d'exécution était
    # la seule pièce du PnL adossée à aucune observation (FIXED_SLIPPAGE_BPS =
    # 2,0 bps pour tous les symboles et tous les régimes). Elle accumule la
    # distribution par symbole qui permettra de remplacer cette constante par
    # une mesure. Non bloquante : une sonde ratée se rattrape au cycle suivant,
    # et il vaut mille fois mieux un trou déclaré qu'un spread inventé.
    spread_rec = run_step("PROBE_SPREAD_CROSS_SECTION", SPREAD_PROBE_SCRIPT, 120)

    steps, skipped = [], []
    for r in runners:
        alpha_id = r["alpha_id"]
        if args.only and alpha_id != args.only:
            continue
        if not is_due(alpha_id, int(r["cadence_minutes"]), last_success, args.force):
            skipped.append(alpha_id)
            continue
        rec = run_step(alpha_id, r["script"], int(r["timeout_sec"]))
        rec["role"] = r.get("role")
        steps.append(rec)
        if rec["status"] == "OK":
            last_success[alpha_id] = cycle_started.isoformat()

    if skipped:
        print(f"[cycle] cadence non due, sautés : {', '.join(skipped)}", flush=True)

    producers_ok = sum(1 for s in steps if s["status"] == "OK")
    producers_failed = [s["name"] for s in steps if s["status"] != "OK"]

    # Étape 2 — étiquetage provenance. Toujours exécutée, même si tous les
    # producteurs ont été sautés pour cadence : le freeze_timestamp d'un alpha
    # peut avoir changé dans le registre entre deux cycles, ce qui rebascule du
    # volume déjà écrit entre REPLAY et FORWARD_LIVE sans qu'aucune décision
    # neuve ne soit produite. Le script est idempotent et ne touche que cette
    # colonne.
    provenance_rec = run_step("APPLY_PROVENANCE_TAGS", PROVENANCE_SCRIPT, 600)

    # Étape 2b — scellement des résultats. Non bloquante DÉLIBÉRÉMENT : un
    # labelliseur cassé ne doit pas arrêter le paper trading, et sa panne n'est
    # pas silencieuse (les labels rattrapés plus tard sortent LATE_BACKFILL,
    # visible dans outcomes.parquet et au scoreboard). L'inverse — bloquer le
    # cycle sur la mesure — ferait perdre de la collecte pour sauver de la
    # lecture, alors que la collecte, elle, ne se rattrape pas.
    outcome_rec = run_step("LABEL_FORWARD_OUTCOMES", OUTCOME_LABEL_SCRIPT, 900)

    portfolio_rec = None
    if not args.skip_portfolio:
        # Le portefeuille tourne MÊME si des producteurs ont échoué : il agrège
        # les ledgers sur disque, qui restent valides et complets jusqu'à leur
        # dernière écriture réussie. Le sauter punirait tous les alphas sains
        # pour la panne d'un seul.
        portfolio_rec = run_step("PORTFOLIO_SHADOW", PORTFOLIO_SCRIPT, 1800)

    scoreboard_recs = []
    if not args.skip_scoreboards:
        for sb in SCOREBOARD_SCRIPTS:
            scoreboard_recs.append(run_step(Path(sb).stem, sb, 900))

    duration = round((now() - cycle_started).total_seconds(), 2)
    portfolio_ok = portfolio_rec is None or portfolio_rec["status"] == "OK"
    provenance_ok = provenance_rec["status"] == "OK"
    # Un échec d'étiquetage est bloquant au même titre qu'un portefeuille cassé :
    # le portefeuille tournerait alors sur un ensemble forward tronqué (les
    # décisions neuves non étiquetées sont silencieusement écartées), ce qui
    # produirait un état de paper trading faux plutôt qu'absent — bien pire.
    status = "OK" if (portfolio_ok and provenance_ok and not producers_failed) else (
        "DEGRADED" if (portfolio_ok and provenance_ok) else "FAILED")

    state = {
        "cycle_started_at": cycle_started.isoformat(),
        "cycle_finished_at": now().isoformat(),
        "duration_sec": duration,
        "status": status,
        "free_gb": round(free, 2),
        "registry_drift": drift,
        "producers_run": len(steps),
        "producers_ok": producers_ok,
        "producers_failed": producers_failed,
        "producers_skipped_cadence": skipped,
        "steps": steps,
        "collector": collector_rec,
        "spread_probe": spread_rec,
        "provenance_tagging": provenance_rec,
        "outcome_labeling": outcome_rec,
        "portfolio": portfolio_rec,
        "scoreboards": scoreboard_recs,
        "last_success": last_success,
    }
    STATE_PATH.write_text(json.dumps(state, indent=2))
    with LOG_PATH.open("a") as fh:
        fh.write(json.dumps({k: v for k, v in state.items() if k != "steps"}) + "\n")

    print(f"[cycle] terminé status={status} en {duration:.1f}s — "
          f"{producers_ok}/{len(steps)} producteurs OK"
          + (f", échecs: {', '.join(producers_failed)}" if producers_failed else ""),
          flush=True)

    # FAILED (portefeuille cassé) remonte à systemd ; DEGRADED (un producteur
    # en panne, portefeuille sain) ne doit pas faire clignoter le service en
    # rouge en permanence — c'est visible dans CYCLE_STATE.json et le scoreboard.
    return 0 if status in ("OK", "DEGRADED") else 1


if __name__ == "__main__":
    sys.exit(main())
