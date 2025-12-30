# Interface Frontend ML Architecture à 5 Niveaux

## Vue d'ensemble

Cette interface graphique représente visuellement l'architecture hiérarchique ML à 5 niveaux pour le trading algorithmique. Elle offre une vue temps réel via WebSocket et permet d'explorer en détail chaque niveau du pipeline de prédiction.

## Architecture ML Représentée

### Niveau 0 : Global Gating
- **Rôle**: Filtre de tradeabilité
- **Algorithme**: Online P² quantile tracking
- **Features**: Realized Return, Realized Volatility, Max Drawdown
- **Output**: Score [0,1] + décision tradeable/non-tradeable

### Niveau 1 : Context Detectors (5 détecteurs TCN)
1. **TradeabilityDetector**: Classification binaire (tradeable/non-tradeable)
2. **DirectionDetector**: 3 classes (down/flat/up)
3. **PatternDetector**: Multi-label (impulse, reversal, breakout, squeeze)
4. **EventDetector**: Détection d'événements rares
5. **PairwiseContextDetector**: Classification de régime (4 classes)

### Niveau 2 : Conditional Specialists
- **Router Network**: Soft (pondéré) ou Hard (seuil)
- **4 Experts TCN**: Impulse, Reversal, Breakout, Squeeze
- **Dual Output**: Prédiction return [H=30] + volatilité [1]

### Niveau 3 : Aggregators
- **EventClassifier**: 4 classes (NORMAL, EVENT_UP, EVENT_DOWN, VOL_SHOCK)
- **PairwiseComparator**: 3 classes (CONSISTENT, WEAKENING, CONTRADICTION)
- **Decision Logic**: CONFIRM / INVALIDATE / DELAY

### Niveau 4 : Meta-Decider (PPO)
- **Algorithm**: Proximal Policy Optimization
- **Actor**: 3 actions (BUY/SELL/WAIT) avec probabilités
- **Critic**: Estimation de valeur + advantage (GAE)
- **Reward**: PnL proxy - error cost - drawdown - turnover

## Fonctionnalités de l'Interface

### Modes de Vue

#### 1. Flow View (Vue Pipeline)
- Visualisation verticale du flux de données
- Connexions animées entre niveaux
- Indicateurs d'état par niveau (actif/processing/error)
- Particules animées représentant le flux de données
- Métriques prévisualisées pour chaque niveau
- Click sur un niveau pour détails

#### 2. Detailed View (Vue Détaillée)
- Grille de cartes expandables
- Tous les niveaux visibles simultanément
- Graphiques ECharts interactifs
- Métriques complètes par niveau

### Temps Réel via WebSocket

L'interface se connecte via WebSocket à `ws://localhost:8000/ws/ml-architecture` pour recevoir:
- Mises à jour architecture globale
- Mises à jour par niveau (level0-level4)
- Mises à jour prédictions
- Statistiques de throughput

**Messages WebSocket:**
```json
{
  "type": "level0_update",
  "payload": {
    "tradeability_score": 0.75,
    "is_tradeable": true,
    ...
  }
}
```

## Structure des Fichiers

```
frontend/alpha-dashboard/src/
├── components/
│   └── MLArchitecture/
│       ├── MLArchitectureView.tsx      # Composant principal
│       ├── ArchitectureFlow.tsx        # Vue pipeline avec canvas
│       ├── Level0Gating.tsx            # Détails Level 0
│       ├── Level1Context.tsx           # Détails Level 1
│       ├── Level2Specialists.tsx       # Détails Level 2
│       ├── Level3Aggregators.tsx       # Détails Level 3
│       ├── Level4MetaDecider.tsx       # Détails Level 4
│       └── MLArchitecture.css          # Styles complets
└── services/
    ├── DataService.ts                  # API REST calls
    └── MLWebSocketService.ts           # Gestion WebSocket

frontend_pipeline/
└── ml_endpoints.py                     # Backend API endpoints
```

## API Endpoints

### REST API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ml/architecture/status` | GET | État global (tous niveaux) |
| `/ml/level0/gating` | GET | Données Level 0 |
| `/ml/level1/contexts` | GET | Données Level 1 |
| `/ml/level2/specialists` | GET | Données Level 2 |
| `/ml/level3/aggregators` | GET | Données Level 3 |
| `/ml/level4/policy` | GET | Données Level 4 |
| `/ml/level/{id}/metrics` | GET | Métriques d'un niveau |
| `/ml/predictions/latest` | GET | Dernière prédiction complète |
| `/ml/flow/throughput` | GET | Stats pipeline throughput |

### WebSocket

```
ws://localhost:8000/ws/ml-architecture
```

**Types de messages:**
- `architecture_update`: Mise à jour globale
- `level0_update`: Niveau 0
- `level1_update`: Niveau 1
- `level2_update`: Niveau 2
- `level3_update`: Niveau 3
- `level4_update`: Niveau 4
- `prediction_update`: Nouvelle prédiction
- `throughput_update`: Stats performance

## Démarrage

### Backend

```bash
cd frontend_pipeline
python api_server.py
```

L'API démarre sur `http://localhost:8000`
Documentation Swagger: `http://localhost:8000/docs`

### Frontend

```bash
cd frontend_pipeline/frontend/alpha-dashboard
npm install
npm start
```

L'application démarre sur `http://localhost:3000`

### Accès à l'Interface ML

1. Ouvrir `http://localhost:3000`
2. Cliquer sur "ML Architecture" dans la navigation
3. Toggle "Live Mode" pour activer les mises à jour temps réel
4. Choisir "Flow View" ou "Detailed View"

## Intégration avec Modèles Réels

Actuellement, l'API utilise des **données mock** générées aléatoirement. Pour connecter aux vrais modèles:

### 1. Remplacer les générateurs dans `ml_endpoints.py`

```python
# Au lieu de generate_level0_data()
def generate_level0_data():
    # Charger le vrai modèle Level 0
    from ai.models.gatingGlobal.level0_gating_global import Level0GatingGlobal

    model = Level0GatingGlobal.load_from_checkpoint('path/to/checkpoint')

    # Obtenir les features actuelles
    features = get_current_features()

    # Inférence
    output = model.forward(features)

    return {
        "tradeability_score": output.score.item(),
        "is_tradeable": output.is_tradeable,
        "threshold": model.threshold,
        ...
    }
```

### 2. Pipeline de Prédiction Continue

Pour un vrai système temps réel, implémenter un pipeline qui:

```python
# pipeline/ml_inference_pipeline.py
import asyncio
from ml_endpoints import manager

async def continuous_inference():
    while True:
        # 1. Récupérer les dernières données de marché
        market_data = await fetch_latest_market_data()

        # 2. Calculer les features
        features = compute_features(market_data)

        # 3. Inférence sur tous les niveaux
        level0_out = level0_model(features)
        level1_out = level1_model(features, level0_out)
        level2_out = level2_model(features, level1_out)
        level3_out = level3_model(level2_out)
        level4_out = level4_model(level0_out, level1_out, level2_out, level3_out)

        # 4. Broadcast via WebSocket
        await manager.broadcast({
            "type": "architecture_update",
            "payload": {
                "level0": level0_out.to_dict(),
                "level1": level1_out.to_dict(),
                "level2": level2_out.to_dict(),
                "level3": level3_out.to_dict(),
                "level4": level4_out.to_dict()
            }
        })

        await asyncio.sleep(2)  # 2 secondes entre mises à jour
```

### 3. Charger les Checkpoints

```python
# config.py
CHECKPOINTS = {
    "level0": "ai/models/runs/level0_gating_best.ckpt",
    "level1": {
        "tradeability": "ai/models/runs/l1_tradeability.ckpt",
        "direction": "ai/models/runs/l1_direction.ckpt",
        "pattern": "ai/models/runs/l1_pattern.ckpt",
        "event": "ai/models/runs/l1_event.ckpt",
        "pairwise": "ai/models/runs/l1_pairwise.ckpt",
    },
    "level2": "ai/models/runs/level2_specialists.ckpt",
    "level3": {
        "event": "ai/models/runs/l3_event_classifier.ckpt",
        "pairwise": "ai/models/runs/l3_pairwise_comparator.ckpt"
    },
    "level4": "ai/models/runs/level4_ppo_policy.ckpt"
}
```

## Personnalisation

### Couleurs par Niveau

Dans [MLArchitecture.css](frontend_pipeline/frontend/alpha-dashboard/src/components/MLArchitecture/MLArchitecture.css:4-9):

```css
--level0-color: #FF6B6B  /* Rouge - Gating */
--level1-color: #4ECDC4  /* Turquoise - Context */
--level2-color: #45B7D1  /* Bleu - Specialists */
--level3-color: #96CEB4  /* Vert - Aggregators */
--level4-color: #FFEAA7  /* Jaune - Meta */
```

### Fréquence de Mise à Jour

Dans [ml_endpoints.py](frontend_pipeline/ml_endpoints.py:410):

```python
# Modifier la fréquence de broadcast (ligne ~410)
await asyncio.sleep(2)  # 2 secondes actuellement
```

Dans [MLArchitectureView.tsx](frontend_pipeline/frontend/alpha-dashboard/src/components/MLArchitecture/MLArchitectureView.tsx:31):

```typescript
// Si utilisation polling au lieu de WebSocket
const interval = setInterval(loadArchitectureData, 5000); // 5s
```

## Métriques Affichées

### Level 0
- ✓ Tradeability Score
- ✓ Is Tradeable (status)
- ✓ Threshold causal
- ✓ Features (R, RV, DD)
- ✓ Quantiles P² (P10, P50, P90)
- ✓ Historique scores

### Level 1
- ✓ 5 détecteurs avec confidence
- ✓ Direction prédite (down/flat/up)
- ✓ Patterns actifs (multi-label)
- ✓ Régime de marché (4-class)
- ✓ Événements détectés

### Level 2
- ✓ Mode router (soft/hard)
- ✓ Poids par expert
- ✓ Expert actif
- ✓ Return prédit par expert
- ✓ Volatilité prédite par expert
- ✓ Return agrégé
- ✓ Volatilité agrégée

### Level 3
- ✓ Classe événement (4-class)
- ✓ Probabilités événements
- ✓ Classe pairwise (3-class)
- ✓ Consensus score
- ✓ Décision (CONFIRM/INVALIDATE/DELAY)
- ✓ Historique événements

### Level 4
- ✓ Probabilités actions (BUY/SELL/WAIT)
- ✓ Action sélectionnée
- ✓ Value estimate (critic)
- ✓ Advantage
- ✓ Reward components décomposés
- ✓ Historique trades
- ✓ Performance (PnL, Sharpe, Win Rate, DD)

## Diagramme de Flux

```
┌─────────────────────┐
│   Raw Market Data   │
│   (OHLCV + 48 feat) │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   Level 0: Gating   │ → Tradeability Score [0,1]
│   (P² Quantile)     │ → Tradeable: Yes/No
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Level 1: Contexts   │ → 5 Detectors Output
│ (5x TCN Networks)   │ → Direction, Patterns, Events, Regime
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Level 2: Specialists│ → Router weights [4]
│ (Router + 4 Experts)│ → Return pred [30], Vol pred [1]
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Level 3: Aggregators│ → Event Class (4-class)
│ (Event + Pairwise)  │ → Pairwise Class (3-class)
└──────────┬──────────┘ → Decision: CONFIRM/INVALIDATE/DELAY
           │
           ▼
┌─────────────────────┐
│ Level 4: Meta (PPO) │ → Action: BUY/SELL/WAIT
│ (Actor-Critic RL)   │ → Confidence [0,1]
└──────────┬──────────┘ → Expected Return
           │
           ▼
┌─────────────────────┐
│   Final Action      │
│   + Confidence      │
└─────────────────────┘
```

## Performance

L'interface est optimisée pour:
- **Réactivité**: Memoization des composants React
- **WebSocket**: Reconnexion automatique (max 5 tentatives)
- **Canvas**: Animations fluides avec requestAnimationFrame
- **Charts**: ECharts en mode dark optimisé

## Dépendances

### Frontend
- React 19.2.0
- TypeScript 4.9.5
- ECharts 6.0.0 (visualisations)
- Axios 1.13.2 (fallback HTTP)

### Backend
- FastAPI (API REST + WebSocket)
- Python 3.9+
- PyTorch (pour modèles ML)
- Pandas, NumPy (data processing)

## Troubleshooting

### WebSocket ne se connecte pas

```bash
# Vérifier que l'API est lancée
curl http://localhost:8000/

# Vérifier les endpoints ML
curl http://localhost:8000/ml/architecture/status
```

### Erreur CORS

Le backend est configuré pour accepter `http://localhost:3000`. Si autre port:

```python
# api_server.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:VOTRE_PORT"],
    ...
)
```

### Données ne s'affichent pas

1. Vérifier la console navigateur (F12)
2. Vérifier les erreurs API côté backend
3. Tester les endpoints manuellement:

```bash
curl http://localhost:8000/ml/level0/gating
curl http://localhost:8000/ml/level1/contexts
```

## Prochaines Étapes

1. **Connexion aux Modèles Réels**
   - Remplacer mock data par inférence réelle
   - Charger checkpoints entraînés
   - Pipeline continu d'inférence

2. **Optimisations**
   - Lazy loading des charts
   - Virtual scrolling pour historiques
   - Compression WebSocket

3. **Nouvelles Fonctionnalités**
   - Téléchargement historique
   - Comparaison multi-symboles
   - Alerts configurables
   - Mode replay (backtesting)

## Licence

Propriétaire - Tous droits réservés

## Contact

Pour questions techniques sur l'interface ML Architecture, référez-vous à ce README ou aux commentaires dans le code source.

---

**Version**: 1.0.0
**Date**: 2025-12-21
**Auteur**: Claude AI + Équipe de Développement
