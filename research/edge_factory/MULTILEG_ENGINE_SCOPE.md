# MultiLegResearchEngine — scope minimal (Phase 0)

Écrit après les trois inventaires de step 3 (`funding_relative_value_cross_venue`,
`calendar_basis_v1`, `cross_sectional_momentum_v1`) — pas avant. Ce document ne
scope que ce que les faits observés justifient réellement, pas un framework
spéculatif.

**Règle imposée** : aucun composant Phase 0 n'est créé s'il n'est pas requis par
au moins deux des trois moteurs préenregistrés, ou par la première tranche
verticale complète (`funding_relative_value_cross_venue_v1`, choisie en premier
car son inventaire est le plus proche de `DATA_READY` — voir la priorité déjà
actée : funding RV → momentum → calendar basis).

## Ce qui existe déjà et ne sera pas reconstruit

Avant de scoper quoi que ce soit de neuf : `src/alpha20/validation/promotion_gate.py`
contient déjà `deflated_sharpe_ratio()`, `pbo_cscv()`, `gate_sleeve()` et
`gate_research()` avec les seuils DSR/PBO déjà chargés depuis la config du
programme. Aucun des trois moteurs ne doit réimplémenter ces calculs — le
moteur produit des séries compatibles avec ces fonctions, un point c'est tout.
`src/alpha20/validation/experiment_registry.py` existe aussi et n'est pas
recréé ici.

## Les cinq interfaces

### 1. `InstrumentMaster`

Justifié par 3/3 moteurs : funding RV a besoin d'identifier un instrument à
travers 3 venues (Binance/Bybit/Hyperliquid, perp uniquement) ; calendar basis
a besoin d'un instrument avec échéance réelle (`contracts.json` donne les
dates d'expiration des trimestriels Binance) ; momentum a besoin d'identifier
chacun des 787 perps du store `um_klines_1d` avec leur date de listing/délisting
réelle.

```text
InstrumentMaster
  venue: str              # binance | bybit | hyperliquid
  symbol: str              # BTC, ETH, ...
  instrument_type: str     # perp | dated_future
  expiry: date | None      # depuis contracts.json pour dated_future ; None pour perp
  listed_from: date
  delisted_at: date | None # depuis um_klines_1d pour le store momentum
```

Explicitement exclu maintenant : type `option` (options_flow est fermé
NO_EDGE, aucun sleeve options vivant), type `dex_pool` (moteur 6 non
commencé) — à ajouter seulement quand un de ces moteurs redevient actif.

### 2. `PointInTimeUniverse`

Justifié directement par momentum (univers 787 symboles, délistements
réels vérifiés : LUNAUSDT, BTTUSDT, LENDUSDT...). Funding RV et calendar
basis utilisent aujourd'hui une liste fixe courte (4 et 2 actifs) — l'interface
doit accepter une liste statique comme cas dégénéré trivial, pour que la
tranche verticale funding RV puisse l'implémenter sans logique de membership
variable dans le temps.

```text
PointInTimeUniverse
  as_of(date) -> list[Instrument]   # membership à une date donnée, sans lookahead
```

Réutilise `build_membership()` déjà écrit dans
`scripts/backtest_ctrend_v1.py` (volume médian 30j décalé t-1, ≥5M$, ≥31j
d'historique) — ne pas réimplémenter, envelopper.

Explicitement exclu maintenant : classification sectorielle/factorielle
(la neutralisation secteur de momentum est son étape 7, pas encore commencée).

### 3. `CostModel`

Justifié par 3/3 moteurs — et par la tranche verticale. Chaque inventaire a
trouvé soit des coûts réels (funding archivé), soit des estimations forfaitaires
non empiriques (23 bps calendar basis, 15 bps + 8 %/an momentum, 5-5,5 bps +
2 bps slippage funding RV depuis `FUNDING_XVENUE_V0`) — jamais une vraie table
de frais complète. Le modèle doit donc distinguer explicitement le réel de
l'assumé.

```text
CostModel
  fee(venue, instrument, side) -> bps          # réel si connu, sinon assumption taguée
  funding_or_carry(instrument, ts) -> rate      # réel pour les perps archivés ; n/a sinon
  borrow(symbol) -> rate                        # défaut plat 8%/an (src/alpha20/costs/borrow_registry.py)
  slippage(venue, instrument, size) -> bps      # assumption plate, pas de modèle de profondeur
  is_real: bool   # True seulement si la valeur vient de données archivées, pas d'une constante
```

Explicitement exclu maintenant : modèle de slippage par profondeur de carnet
(nécessiterait des données L2, absentes des trois inventaires — c'est le
rôle de la piste `l2_execution`, toujours gatée en overlay sur un edge déjà
positif).

### 4. `MultiLegOrder`

Justifié par la tranche verticale : funding RV = 2 jambes (long/short perp),
calendar basis = 2 jambes (near/far ou perp/dated), momentum = 2 jambes au
niveau portefeuille (décile long / décile short).

```text
MultiLegOrder
  legs: list[Leg]        # chacune: instrument, side, size, venue
  delta_target: float    # 0 pour les trois moteurs actuels (tous market-neutral)
```

Explicitement exclu maintenant : simulation de fills partiels / latence
(rôle de `l2_execution`, non construit).

### 5. `MultiLegBacktestResult`

Justifié par la tranche verticale et par un besoin transversal déjà visible :
chaque piste fermée (ctrend, top_traders, basis_dispersion...) rapporte déjà
CAGR/maxDD/détail par année dans une forme ad hoc légèrement différente à
chaque fois. Ce n'est pas un nouveau calcul — c'est une forme de sortie
commune, alimentée dans `gate_sleeve()`/`gate_research()` existants plutôt que
recalculée.

```text
MultiLegBacktestResult
  trades: DataFrame
  pnl_daily: Series
  per_year: dict[str, float]
  net_events: Series          # entrée directe de gate_sleeve()
  net_events_x2: Series       # entrée directe de gate_sleeve()
  returns_for_dsr: Series     # entrée directe de gate_research()
  trials_matrix: DataFrame | None   # entrée directe de pbo_cscv(), None si pas de grille testée
```

Explicitement exclu maintenant : aucun calcul DSR/PBO propre — délégué à
`promotion_gate.py`, qui existe déjà et ne sera pas dupliqué.

## Ce qui n'est PAS scopé maintenant

`FeeModel`/`FundingModel`/`BorrowModel` séparés, `MarginModel`,
`MultiLegExecutionSimulator`, `PortfolioMarginEngine` : aucun des trois
inventaires n'a fait apparaître un besoin réel de marge dynamique ou de
simulateur d'exécution distinct — toutes les données de coût trouvées
tiennent dans `CostModel` tel que scopé ci-dessus. Ces composants seront
ajoutés seulement quand un moteur (options VRP en particulier, qui aura
vraiment besoin d'un moteur de marge portefeuille) le demandera concrètement.

## Prochaine étape (pas encore commencée)

Implémenter ces cinq interfaces comme une première tranche verticale sur
`funding_relative_value_cross_venue_v1` uniquement (son inventaire est le
plus proche de complet : funding réel sur 3 venues, seul le prix perp
Bybit/Hyperliquid manque). Ne généraliser vers calendar basis et momentum
qu'une fois cette tranche verticale qui fonctionne end-to-end.
