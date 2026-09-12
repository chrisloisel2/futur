# Politique globale des features (par feature, jamais par événement)

Un ban par événement fondé sur un score bruité serait un filtre dépendant des données : la surface survivante serait biaisée. La règle est donc globale.

| feature | classe | autorisée comme | pourquoi |
|---|---|---|---|
| pre_binance_return_30d/14d/7d/3d/24h | price_only | **conditioning** | un prix ne se gonfle pas par wash ; bougies completes closes <= t0 (daily : <= dernier minuit UTC) |
| pre_binance_volatility_7d, range_7d, max_drawdown_7d, pump_score | price_only | **conditioning** | derives de prix seulement |
| pre_binance_volume_7d, volume_24h, liquidity_proxy | volume_derived | **covariate** | gonflables par un programme ; jamais variable de conditionnement ni d'exclusion pour MEXC_FIRST |
| pre_binance_volume_* en USD absolu | volume_derived | **banned_cross_venue** | un niveau de volume n'est comparable ni entre places ni entre regimes de frais ; seuls des rangs intra-MEXC sont lisibles |
| pre_binance_exhaustion_score | mixed | **covariate** | 40 % du score est un terme de volume |
| volume_floor, volume_cv, volume_range_coupling, log10_volume_per_range | wash_covariate | **covariate** | non valides ; rangs intra-MEXC ; controle de robustesse, jamais un filtre |
| volume_anomaly_rank, wash_volume_suspect, programme_like | wash_covariate | **covariate** | fraction pre-declaree (decile) ; un ban par evenement serait un filtre dependant des donnees |
| anticipation_ratio | anticipation | **covariate** | mesure une anticipation (information), pas un wash ; hors du drapeau par construction |
| venue_volume_multiple, coupling_gap (meme actif, second venue) | same_asset_control | **covariate** | seule quantite controlee par l'actif ; descriptive |

- `conditioning` : peut être variable de conditionnement d'une prereg (jamais plus d'une par prereg).
- `covariate` : descriptif / contrôle de robustesse ; jamais un filtre d'inclusion.
- `banned_cross_venue` : jamais comparé entre places ; rangs intra‑MEXC seulement.

Aucun événement n'est exclu par cette politique. Les événements `BAD_TIMESTAMP` sont exclus par la règle P10/P11, pas par le volume.
