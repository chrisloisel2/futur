# Alpha Trading Platform - Frontend Guide

## Vue d'ensemble

Le frontend a été complètement refondu avec un design ultra-professionnel et des fonctionnalités avancées :

### 🎨 **Design System Professionnel**
- Palette de couleurs sophistiquée (bleu/violet)
- Typographie élégante avec hiérarchie claire
- Animations subtiles et micro-interactions
- Glass morphism et effets de profondeur
- Responsive design complet

### 📊 **Dashboard Principal**
- Vue d'ensemble du marché en temps réel
- Métriques clés : Records totaux, signaux alpha, sources de données
- Statut WebSocket en direct
- Marché crypto avec toutes les paires
- Fear & Greed Index
- Analyse des signaux (bullish/bearish/neutral)

### 🗂️ **Dataset Explorer (S3)**
- **Visualisation complète de votre dataset S3**
- Explore toutes les années disponibles (2017-2025)
- Affiche tous les symboles par année
- Recherche de symboles
- Graphiques interactifs candlestick + volume
- Statistiques détaillées (min, max, avg, volume total)
- Cache local pour performances optimales

### 🤖 **AI Predictions (Temps Réel)**
- **Prédictions seconde par seconde**
- Démarrage/arrêt du pipeline de prédiction
- Cartes de prédiction pour chaque devise
- Prix actuel vs prix prédit
- Indicateur de confiance (confidence score)
- Direction (up/down/neutral)
- Historique de prédictions avec graphiques
- Mise à jour en temps réel (polling 1s)

## 🚀 Démarrage

### 1. Backend API

```bash
cd /Users/christopher/Desktop/futur/frontend_pipeline
python api_server.py
```

Le serveur démarre sur `http://localhost:8000`

**Nouveaux endpoints S3 :**
- `GET /s3/overview` - Vue d'ensemble du dataset S3
- `GET /s3/years` - Liste des années disponibles
- `GET /s3/symbols/{year}` - Symboles pour une année
- `GET /s3/data/{symbol}/{year}` - Données d'un symbole
- `GET /s3/latest/{symbol}` - Dernières données disponibles

**Endpoints prédictions :**
- `POST /pipeline/start` - Démarrer le pipeline de prédiction
- `POST /pipeline/stop` - Arrêter le pipeline
- `GET /pipeline/status` - Statut du pipeline
- `GET /pipeline/predictions` - Toutes les prédictions actuelles
- `GET /pipeline/prediction/{symbol}` - Prédiction pour un symbole

### 2. Frontend React

```bash
cd /Users/christopher/Desktop/futur/frontend_pipeline/frontend/alpha-dashboard
npm install  # Si première installation
npm start
```

L'application démarre sur `http://localhost:3000`

## 📁 Structure du Code

```
frontend/alpha-dashboard/src/
├── App.tsx                          # Navigation principale
├── App.css                          # Styles globaux + navigation
├── styles/
│   └── designSystem.ts             # Système de design (couleurs, typo, spacing)
├── components/
│   ├── Dashboard.tsx               # Dashboard principal (refonte)
│   ├── Dashboard.css               # Styles modernes
│   ├── S3DataExplorer.tsx          # Explorer les données S3
│   ├── S3DataExplorer.css          # Styles S3 Explorer
│   ├── RealtimePredictions.tsx     # Prédictions temps réel
│   ├── RealtimePredictions.css     # Styles prédictions
│   ├── CryptoMarket.tsx            # Marché crypto (existant)
│   ├── WebsocketStatus.tsx         # Statut WebSocket (existant)
│   └── charts/                     # Composants graphiques
└── services/
    └── DataService.ts              # Service API (mis à jour)
```

## 🎯 Fonctionnalités Clés

### Navigation
La barre de navigation en haut permet de basculer entre 3 vues :
- **Dashboard** 📊 : Vue d'ensemble du marché
- **Dataset Explorer** 🗂️ : Exploration S3 complète
- **AI Predictions** 🤖 : Prédictions en temps réel

### S3 Dataset Explorer

**Sélection de l'année :**
- Boutons pour chaque année disponible
- Affichage du nombre de symboles par année

**Recherche de symboles :**
- Recherche instantanée (ex: "BTC", "ETH")
- Filtrage en temps réel

**Visualisation :**
- Graphique candlestick professionnel
- Volume en bas du graphique
- Tooltip interactif avec OHLCV
- Statistiques : min, max, avg, volume total, dates

**Performances :**
- Cache local pour éviter les re-téléchargements
- Limite de 5000 points par défaut pour la fluidité
- Chargement optimisé depuis S3

### AI Predictions

**Démarrage :**
1. Cliquer sur "Start Pipeline"
2. Attendre 2-3 secondes pour l'initialisation
3. Les prédictions commencent à s'afficher

**Cartes de prédiction :**
- **Symbole** : Nom de la devise (ex: BTC/USDT)
- **Direction** : ↗ (up), ↘ (down), → (neutral)
- **Prix actuel** : Prix du marché en temps réel
- **Prix prédit** : Prédiction du modèle AI
- **Change %** : Variation en pourcentage
- **Confidence** : Barre de confiance avec couleur (vert > 70%, amber > 40%, rouge < 40%)

**Graphique d'historique :**
- Cliquer sur une carte pour voir l'historique
- Graphique ligne temps réel vs prédiction
- Historique des 100 dernières prédictions

## 🎨 Design System

Le design system est défini dans `src/styles/designSystem.ts` :

**Couleurs principales :**
- Background : `#0A0E1A` (dark blue)
- Accent Primary : `#3B82F6` (blue)
- Accent Secondary : `#8B5CF6` (purple)
- Success : `#10B981` (green)
- Error : `#EF4444` (red)

**Effets :**
- Glass morphism : `background: rgba(255, 255, 255, 0.03)` + `backdrop-filter: blur(10px)`
- Ombres subtiles avec glow coloré
- Transitions douces : `200ms cubic-bezier(0.4, 0, 0.2, 1)`
- Hover effects avec translateY

**Responsive :**
- Breakpoints : sm (640px), md (768px), lg (1024px), xl (1280px)
- Grid adaptatif
- Navigation mobile avec flex-wrap

## 🛠️ Configuration Backend (API Server)

Le backend a été étendu avec de nouveaux endpoints S3. Assurez-vous que :

1. **AWS Credentials** sont configurés :
   ```bash
   export AWS_PROFILE=your_profile
   # ou
   aws configure
   ```

2. **Bucket S3** accessible : `qbia`
3. **Prefix** : `bourse/mintrad`
4. **Format de données** : Parquet files avec structure Binance klines

## 📊 Performance

**Optimisations :**
- Cache local S3 dans `/tmp/trading_data_cache`
- Limite de données frontend (5000 points par défaut)
- Polling optimisé pour prédictions (1s)
- Lazy loading des graphiques
- Mémorisation des composants React

**Métriques attendues :**
- Chargement initial : < 2s
- Navigation entre vues : instantanée
- Chargement données S3 (cache hit) : < 100ms
- Chargement données S3 (cache miss) : 2-5s
- Update prédictions : 1s

## 🐛 Debugging

**API non accessible :**
```bash
# Vérifier que l'API tourne
curl http://localhost:8000/health

# Logs API
python api_server.py
```

**S3 non accessible :**
```bash
# Tester les credentials
python ai/test_s3_data_source.py

# Vérifier l'accès bucket
aws s3 ls s3://qbia/bourse/mintrad/
```

**Frontend ne démarre pas :**
```bash
cd frontend_pipeline/frontend/alpha-dashboard
rm -rf node_modules package-lock.json
npm install
npm start
```

## 🎯 Prochaines Étapes

Améliorations possibles :
- [ ] WebSocket pour prédictions (remplacer polling)
- [ ] Export des données en CSV/JSON
- [ ] Comparaison multi-symboles
- [ ] Backtesting des prédictions
- [ ] Alertes personnalisées
- [ ] Dark/Light mode toggle
- [ ] Favoris pour symboles

## 📝 Notes Importantes

- **Ne pas commiter les credentials AWS**
- Le cache S3 peut prendre de l'espace disque (surveiller `/tmp`)
- Les prédictions nécessitent que le pipeline soit démarré manuellement
- Certains symboles peuvent ne pas avoir de données pour certaines années

---

**Bon trading ! 🚀📈**
