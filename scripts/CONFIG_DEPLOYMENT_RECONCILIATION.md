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

## Ce qui N'est PAS fait ici (décision humaine requise avant d'aller plus loin)

Le mécanisme complet demandé —

```text
commit approuvé
→ artefact de config signé/hashé
→ déploiement atomique qbee
→ vérification du hash au démarrage
→ refus de démarrer si le hash live diffère
```

— nécessite de modifier le **code de démarrage de l'orchestrateur du
tournoi**, un système qui tourne actuellement en direct sur qbee. Ce n'est
pas fait dans ce commit : c'est un changement plus invasif sur du code
d'exécution live, qui mérite une confirmation explicite avant d'y toucher,
pas une décision prise en cours d'audit d'un moteur de recherche sans
rapport direct. Le diagnostic ci-dessus est le préalable neutre (constater
la dérive) ; la décision de bloquer le démarrage sur dérive de hash est une
étape séparée.

## Prochaine étape

1. Dès que qbee est joignable : lancer `verify_config_deployment.py
   --remote qbee@100.127.59.114`, documenter l'état réel (dérive ou non)
   pour `basis_term_v0` ET pour `carry_solusdt`/`carry_bnbusdt`.
2. Décision humaine séparée : construire ou non le refus de démarrage sur
   hash divergent dans l'orchestrateur.
