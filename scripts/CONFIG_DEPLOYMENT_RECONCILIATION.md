# Réconciliation config Mac/qbee — 2026-07-21

## Incident

Le 2026-07-21, la démotion de `basis_term_v0` (ACTIVE→OBSERVE_ONLY,
`df2d024`) a été appliquée sur le Mac (commitée) et directement sur le
fichier de travail live de qbee (non commitée là-bas, volontairement, pour
ne pas toucher à l'état git d'une branche différente). En vérifiant,
découverte que les démotions du **même jour** de `carry_solusdt` et
`carry_bnbusdt` (déjà commitées sur Mac, `main`) n'avaient **jamais atteint
le fichier live de qbee** avant mon intervention — qbee tourne sur
`feat/free-derivatives-backfill`, une branche qui n'avait pas reçu ces
commits.

**Conséquence** : pendant une durée indéterminée, le dépôt de recherche
affirmait `carry_solusdt`/`carry_bnbusdt` en `OBSERVE_ONLY` (non
sélectionnables) alors que le fichier réellement lu par le tournoi les
gardait `ACTIVE`. Le dépôt et le comportement réel racontaient deux
histoires différentes, sans qu'aucun mécanisme ne le signale.

## Outil construit

[`scripts/verify_config_deployment.py`](verify_config_deployment.py) —
calcule le hash SHA-256 de `configs/alpha20_runners.yaml` et
`configs/alpha20.yaml` localement (au commit HEAD) et sur un hôte distant
via SSH, signale toute divergence. Diagnostic seul : ne déploie rien, ne
modifie rien.

```text
python3 scripts/verify_config_deployment.py --remote qbee@100.127.59.114
```

## Résultat de la réconciliation réelle (2026-07-22, qbee de nouveau joignable)

```text
python3 scripts/verify_config_deployment.py --remote qbee@100.127.59.114
```

**Dérive confirmée** sur les deux fichiers, hash local ≠ hash qbee :

| Fichier | local (commit `5e4f334`) | qbee (branche `feat/free-derivatives-backfill`, commit `2fe693b`) |
|---|---|---|
| `configs/alpha20_runners.yaml` | `7f01a543...` | `aed1d44f...` |
| `configs/alpha20.yaml` | `ed00eccb...` | `123ea25a...` |

Attendu et documenté : qbee tourne sur une branche jamais synchronisée avec
`main`, la dérive porte sur bien plus que le seul changement de ce jour.

**Vérification spécifique** : le champ qui comptait opérationnellement
(`basis_term_v0: status: OBSERVE_ONLY`, appliqué manuellement au fichier de
travail de qbee le 2026-07-21) **est bien toujours en place** —
`grep -A2 'runner_id: basis_term_v0'` sur qbee confirme
`status: OBSERVE_ONLY`. La dérive de hash globale ne signale donc pas une
régression de cette décision précise, seulement l'écart pré-existant plus
large entre les deux branches, non résolu ici.

## Mécanisme de refus au démarrage (2026-07-22, construit et déployé)

Décision humaine explicite reçue : construire le refus de démarrage.
Fait, avec un choix de conception délibéré pour rester sûr :

- `src/alpha20/deployment_guard.py` — `assert_deployment_matches_approved()`,
  compare le hash SHA-256 live de `configs/{alpha20_runners,alpha20}.yaml`
  contre `configs/DEPLOYMENT_MANIFEST.json` (jamais committé — **état
  propre à chaque machine**, pas une vérité partagée Mac/qbee : les deux
  dépôts peuvent légitimement diverger dans leur contenu global, ce garde
  détecte seulement les changements NON approuvés depuis la dernière
  génération volontaire du manifeste sur CETTE machine, il ne tranche pas
  la question plus large de la fusion des deux branches).
- Module séparé de `src/alpha20/guard.py` (garde anti-trading-réel
  existante, déjà testée) plutôt qu'une extension — pour ne jamais risquer
  de casser sa garantie déjà en place.
- Câblé aux 7 points d'entrée qui appellent déjà `assert_paper_only()`
  (orchestrateur, dashboard, réconciliation, sélection, recherche de
  portefeuille) — même endroit, même pattern, `SystemExit(2)` si dérive.
- `scripts/generate_deployment_manifest.py` — à exécuter manuellement
  après toute modification humainement relue des fichiers suivis (jamais
  automatiquement).
- 4 tests (`tests/test_alpha20_deployment_guard.py`) + les 8 tests
  existants de `guard.py` toujours verts (aucune régression).
- Manifeste généré sur qbee depuis son état RÉEL actuel (post-correctif
  `basis_term_v0`) — le garde passe dès maintenant ; testé en conditions
  réelles via `scripts/run_alpha20_tournament_dashboard.py` (exit 0).

## Ce qui reste non résolu

La réconciliation complète Mac/qbee (fusionner réellement les deux
branches divergentes) reste une décision séparée, pas prise ici — ce
garde protège contre la dérive **future** non approuvée sur une machine
donnée, il ne résout pas la divergence **historique** déjà documentée plus
haut entre `main` et `feat/free-derivatives-backfill`.
