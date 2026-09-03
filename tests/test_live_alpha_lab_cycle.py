"""tests/test_live_alpha_lab_cycle.py — garde-fous du cycle de paper trading
continu (scripts/run_live_alpha_lab_cycle.py).

Deux incidents constatés le 2026-09-03 sont couverts ici.

INCIDENT 1 — le laboratoire ne tournait pas du tout.
Les 9 producteurs de signal et la couche portefeuille n'étaient couverts par
aucune unité systemd : la décision forward la plus fraîche datait de 38 heures,
et AMIHUD_ILLIQUIDITY_PREMIUM_V1 -- figé et « lancé en forward » la veille --
n'avait produit aucune décision forward. Garde-fou : tout alpha que le registre
déclare en SIGNAL_SHADOW/EXECUTION_SHADOW doit avoir un producteur déclaré dans
configs/live_alpha_runners.yaml, et ce producteur doit exister sur disque.

INCIDENT 2 — la colonne `provenance` n'était posée par aucun producteur.
Elle n'est écrite que par scripts/apply_provenance_tags.py, qui n'était appelé
qu'à la main. Or run_portfolio_shadow.load_forward_only() et les deux scoreboards
filtrent sur `provenance == "FORWARD_LIVE"` et écartent (fail closed) toute ligne
non étiquetée. En fonctionnement continu sans cette étape, chaque décision neuve
serait restée invisible au portefeuille : le paper trading aurait tourné en
n'ouvrant jamais de position sur un signal frais. Garde-fou : l'étiquetage doit
rester câblé dans le cycle, entre producteurs et portefeuille.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

import scripts.apply_provenance_tags as provenance_tags
import scripts.run_live_alpha_lab_cycle as cycle
from src.institutional.live_alpha_lab import intents

RUNNERS_CFG = ROOT / "configs" / "live_alpha_runners.yaml"
REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"

SHADOW_STATUSES = ("SIGNAL_SHADOW", "EXECUTION_SHADOW")


def _registry_shadow_alphas() -> list:
    reg = yaml.safe_load(REGISTRY.read_text())
    return [a for a in reg["alphas"] if a.get("operational_status") in SHADOW_STATUSES]


def _runners() -> list:
    return yaml.safe_load(RUNNERS_CFG.read_text())["runners"]


def test_every_shadow_alpha_has_a_runner():
    """INCIDENT 1 : un alpha figé sans producteur n'accumule aucune preuve
    forward tout en paraissant « lancé »."""
    declared = {r["alpha_id"] for r in _runners()}
    missing = [a["alpha_id"] for a in _registry_shadow_alphas() if a["alpha_id"] not in declared]
    assert not missing, (
        f"alphas en shadow sans producteur déclaré : {missing}. "
        "Ils n'accumuleront aucune décision forward — les ajouter à "
        "configs/live_alpha_runners.yaml ou sortir leur operational_status de SHADOW."
    )


def test_every_runner_script_exists():
    for r in _runners():
        assert (ROOT / r["script"]).exists(), f"script producteur introuvable : {r['script']}"


def test_cycle_wires_provenance_tagging_between_producers_and_portfolio():
    """INCIDENT 2 : sans cette étape, une décision neuve n'a pas de valeur de
    `provenance` et le portefeuille l'écarte silencieusement."""
    src = (ROOT / "scripts" / "run_live_alpha_lab_cycle.py").read_text()
    assert cycle.PROVENANCE_SCRIPT == "scripts/apply_provenance_tags.py"
    assert (ROOT / cycle.PROVENANCE_SCRIPT).exists()
    i_prov = src.index("PROVENANCE_SCRIPT, ")       # l'appel run_step, pas la constante
    i_pf = src.index("PORTFOLIO_SCRIPT, ")
    assert i_prov < i_pf, (
        "l'étiquetage de provenance doit tourner AVANT la couche portefeuille : "
        "sinon le portefeuille agrège un ensemble forward tronqué."
    )


def test_provenance_tagging_knows_every_shadow_alpha():
    """apply_provenance_tags.py saute explicitement tout alpha absent de sa table
    TIME_COL_BY_ALPHA (il ne devine jamais la colonne temps). Un alpha en shadow
    absent de cette table ne serait donc JAMAIS étiqueté — invisible au
    portefeuille en permanence, sans la moindre erreur levée."""
    missing = [a["alpha_id"] for a in _registry_shadow_alphas()
               if a["alpha_id"] not in provenance_tags.TIME_COL_BY_ALPHA]
    assert not missing, (
        f"alphas en shadow absents de TIME_COL_BY_ALPHA : {missing}. "
        "Leurs décisions ne seraient jamais étiquetées FORWARD_LIVE."
    )


def test_every_shadow_alpha_is_routed_by_the_portfolio():
    """Un alpha de position sans adaptateur d'intent fait lever un KeyError au
    portefeuille (fail closed) ; un gate/overlay doit être listé explicitement
    dans NOT_A_POSITION_ALPHA pour que son absence se lise comme un choix."""
    known = set(intents.ADAPTERS) | set(intents.NOT_A_POSITION_ALPHA)
    missing = [a["alpha_id"] for a in _registry_shadow_alphas() if a["alpha_id"] not in known]
    assert not missing, (
        f"alphas en shadow inconnus du routage portefeuille : {missing}. "
        "Ajouter dans intents.ADAPTERS, ou dans NOT_A_POSITION_ALPHA si c'est voulu."
    )


def test_disk_floor_is_below_collector_floor():
    """Le plancher disque du cycle reste sous celui des collecteurs.

    Volontaire : les collecteurs écrivent ~0,89 Go/jour de données brutes
    irremplaçables, le cycle des parquets de décisions de l'ordre du mégaoctet.
    Couper le cycle en premier ne libérerait rien de mesurable et supprimerait
    la visibilité (scoreboards, état du portefeuille) au pire moment. Ce test
    empêche qu'un futur ajustement de seuil inverse ce rapport par inadvertance.
    """
    import scripts.collect_microstructure_reduced as collector
    assert cycle.MIN_FREE_DISK_GB < collector.MIN_FREE_DISK_GB_DEFAULT
