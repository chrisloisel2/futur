# Audit technique - projet trading

## Synthèse rapide
- Périmètre couvert : cœur `trading-system` (pipeline ML/decision), collectes marché et signaux (`frontend_pipeline`, `scrapers_engine`, `crypto_indicators_scraper`), signaux alternatifs (`news_signal_engine`, `twitter_signal_engine`), frontends et scripts d’orchestration.
- Modèle régimes binaire entraîné avec succès (`train_regime.sh` via `train_all.sh`), artefacts sauvegardés sous `trading-system/artifacts/models/regime/production_binary_v1`.
- Edge forecaster et pipeline d’intégration pas encore revalidés avec la nouvelle architecture binaire/impulse-événement.

## Résultats récents (train_all.sh)
- Données : BTCUSDT 2019-01-01 → 2023-12-31, split temporel avec embargo 60 min, 2.62M lignes brutes.
- Best variant : `logreg` (binaire calm/reversal) – Accuracy 0.9234, Balanced Acc 0.8802, Macro F1 0.8206, Brier 0.0442, ECE 0.0286. Per-class : calm recall 0.9343 / reversal recall 0.8261, PR-AUC reversal 0.8443.
- Gates production passés (accuracy≥0.60, recalls≥0.50, ECE<0.10). Artefacts : `model.pkl`, `threshold.json`, `metrics.json`, `feature_list.json`, `data_contract.json`.
- Prochaine étape mentionnée par le run : entraîner l’Edge Forecaster (`trading-system/train_edge.sh`) puis backtester.

## Structure et complétion par bloc
| Bloc | Périmètre principal | % finition* | Situation observée | Manques pour l’excellence |
| --- | --- | --- | --- | --- |
| Cœur ML régimes (binaire) | `trading-system/train_regime.sh`, `ai/models/training/common/regime_classifier_v2.py`, artefacts `artifacts/models/regime/production_binary_v1` | 85% | Entraînement réussi, gates passés, seuil calibré, features alignées (58). Scripts v3.0 BINARY prêts. | Propager officiellement le nouveau contrat dans l’inférence (`src/pipeline/models/regime/*`), mettre à jour la doc manquante (`ai/models/INDEX.md` etc.), vérifier compatibilité backtests/live. |
| Edge Forecaster | `trading-system/train_edge.sh`, `src/pipeline/models/edge/forecaster.py`, artefacts `artifacts/models/edge/production_v1*` | 60% | Code et artefacts existants mais pas de run récent avec régimes binaires/impulse événement. Overfitting fixes annoncés mais non vérifiés sur les logs fournis. | Relancer l’entraînement avec le nouveau régime, loguer PR-AUC / overfitting ratio, mettre à jour `production_v1_metrics.json`, recalibrer le post-process (`postprocess.py`, `calibrator.py`). |
| Pipeline décision/risque/exécution | `src/pipeline/decision`, `src/pipeline/risk`, `src/pipeline/execution`, tests e2e/integ sous `tests/` | 75% | Couverture de tests large (unit, integration, e2e). Architecture complète (targets → orders → exchange). | Rejouer la suite de tests + backtests avec les nouveaux artefacts binaires, ajuster règles de gating (`models/gating/rules.py`) et méta-contrôle (`pipeline/meta_control`). |
| Backtests & validation | `backtest*.sh`, `tests/integration/test_backtest_engine.py`, `artifacts/backtests` | 70% | Cadre de backtest présent, scripts pour données réelles. | Recalculer les backtests avec le modèle binaire + edge rafraîchi, tracer PnL/drawdown et calibration drift, archiver rapports. |
| Ingestion marché & features | `src/pipeline/data`, `src/pipeline/features`, collecteurs `frontend_pipeline/*.py`, `crypto_indicators_scraper`, S3 loader | 65% | Collecte S3 opérationnelle (logs montrent chargement 2.6M lignes). Multiples collecteurs/dédup validations (`validate_crypto_data.py`, `history_coverage_report.py`). | Normaliser contrats de données entre collecteurs, ajouter contrôles de qualité systématiques (schema + drift) avant entraînement, consolider catalogues de features pour régimes/edge. |
| Signaux alternatifs (news/twitter/whale) | `news_signal_engine`, `twitter_signal_engine`, `scrapers_engine` | 55% | Pipelines complets (collecte → filtres → enrichment → modèles → signals) mais peu d’indications de métriques ou d’intégration dans le pipeline principal. | Mesurer précision/rappel des signaux, définir scoring commun, brancher dans le router de signaux (`src/domain/signal`, `pipeline/decision`) et backtester l’apport marginal. |
| Frontend & API plateforme | `frontend_pipeline/frontend`, `frontend_pipeline/api_server.py`, scripts `start_*` | 50% | Apps/dashboard et API connecteurs présents. | Vérifier que les endpoints consomment les nouveaux artefacts régimes/edge, ajouter tests contractuels d’API, sécuriser déploiements (env/prod profiles). |
| Ops, doc & automatisation | Scripts `CHECK_STATUS.sh`, `VERIFY_BINARY_SETUP.sh`, `docker/`, `Makefile`, docs `docs/diagrams` | 45% | Outils de vérification existent, CI/CD non visible ici, plusieurs docs référencées mais absentes (`ai/models/INDEX.md`, `MIGRATION_GUIDE.md`...). | Restaurer/compléter la doc, ajouter pipelines CI (tests + lint + training smoke), publier diagrammes à jour (architecture binaire, flux edge). |

\*Pourcentages estimés sur la base du code, des artefacts présents et des logs fournis.

## Actions prioritaires (ordre d’impact)
1) **Edge forecaster à jour** : relancer `trading-system/train_edge.sh` avec la nouvelle sortie régime binaire, journaliser métriques (PR-AUC, overfitting ratio) et sauvegarder sous `artifacts/models/edge/production_v2`.
2) **Aligner l’inférence/backtests** : mettre à jour `src/pipeline/models/regime/*` et `pipeline/meta_control` pour utiliser `production_binary_v1`, puis rejouer `tests/e2e` et `./backtest_real_data.sh`.
3) **Contrats de données** : fixer un `data_contract.json` partagé pour marché/alt-data, valider via `frontend_pipeline/validate_crypto_data.py` avant tout entraînement; ajouter tests de drift/qualité automatisés.
4) **Intégrer les signaux alternatifs** : mesurer la qualité de `news_signal_engine` et `twitter_signal_engine`, brancher leurs scores dans le router de signaux et backtester l’apport marginal.
5) **Doc & CI** : régénérer les guides manquants (INDEX, MIGRATION), compléter `docs/diagrams/*.mmd`, et automatiser une CI qui lance unit+integration tests + une smoke d’entraînement (échantillon réduit).

