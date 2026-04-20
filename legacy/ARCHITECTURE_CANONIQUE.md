# Architecture Canonique

## Décision

Le point d'entrée unique du training est `train.py`.

Le profil de production court terme est `python train.py pipeline ...`.

Statut des autres entrées :

- `train_1m.py` : implémentation interne du profil `python train.py 1m ...`.
- `train_local.py` : implémentation interne du profil `python train.py local ...`.
- `train_pipeline.py` : implémentation interne du profil `python train.py pipeline ...`.
- `ai/models/*` : legacy research.
- `ai/_pipeline.py` : legacy/orchestration partielle, non source de vérité prod.
- `frontend_pipeline/ml_endpoints.py` et composants mock : à retirer ou reconnecter progressivement aux vrais artefacts.

## Convention prod

- Timeframe central : `1h`
- Horizon officiel : `60 minutes`
- Run root : `runs/pipeline/<run_id>/`
- Script de training officiel : `python train.py ...`
- Artefacts de live et dashboard : chargés depuis ce run root uniquement
- Settings et chemins applicatifs : `core/settings.py`

## Labels officiels

Les labels générés par le profil `pipeline` (`python train.py pipeline ...`) sont les seuls labels de production :

- `tradeable_net` : filtre de tradabilité global
- `y_long` : opportunité exploitable long
- `y_short` : opportunité exploitable short

Le fichier `labels.json` contient les statistiques du run, pas les séries complètes.

## Contrat d’artefacts

Chaque run canonique produit :

```text
runs/pipeline/<run_id>/
├── manifest.json
├── config.json
├── labels.json
├── filter/
│   ├── model.pkl
│   ├── scaler.pkl
│   ├── metadata.json
│   ├── filter_model.pkl          # compat legacy
│   └── filter_scaler.pkl         # compat legacy
├── edge_long/
│   ├── model.pkl
│   ├── scaler.pkl
│   ├── metadata.json
│   └── best_model.pkl            # compat legacy
├── edge_short/
│   ├── model.pkl
│   ├── scaler.pkl
│   ├── metadata.json
│   ├── calibrator.pkl            # optionnel
│   └── best_model.pkl            # compat legacy
├── regime/
│   ├── model.pkl
│   ├── scaler.pkl
│   ├── metadata.json
│   └── bear_regime_model.pkl     # compat legacy
├── risk/
│   └── config.json
├── backtest_long/
│   └── summary.json
├── backtest_short/
│   └── summary.json
└── backtest_combined/
    └── summary.json
```

## Fichiers de vérité

- `manifest.json` : layout du run et état d’activation des composants
- `config.json` : configuration exécutable du run
- `core/settings.py` : chemins racine et variables d’environnement partagées
- `filter/metadata.json` : features et seuils du filtre
- `edge_long/metadata.json` : features et seuil long
- `edge_short/metadata.json` : features, seuil short et flag `enabled_for_inference`
- `regime/metadata.json` : features et seuil du gate bear
- `risk/config.json` : paramètres RiskController retenus pour le run

## Règles de chargement live

- Le live doit charger le dernier run valide dans `runs/pipeline/`.
- Il doit lire `manifest.json` et/ou `metadata.json` avant de supposer qu’un composant est actif.
- Un artefact short présent sur disque mais marqué `enabled_for_inference=false` ne doit pas être utilisé.
- Les listes de features doivent venir des métadonnées du run, jamais d’une liste codée en dur divergente.

## Différence prod vs R&D

- Prod : `python train.py pipeline ...` + contrat d’artefacts ci-dessus.
- R&D : tout script qui n’écrit pas ce contrat ou qui ne respecte pas `1h / 60 min`.

## Dette restante

- Centraliser les secrets et la config runtime dans un module settings unique.
- Supprimer les endpoints mock du dashboard ou les reconnecter aux artefacts canoniques.
- Unifier le moteur risk du sous-projet `trading-system` avec celui utilisé par le profil `pipeline`.
