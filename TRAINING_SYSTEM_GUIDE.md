# 🎓 Guide du Système de Gestion d'Entraînement

## Vue d'ensemble

Votre système de trading dispose maintenant d'un système complet de gestion d'entraînement de modèles IA directement depuis l'interface web. Vous pouvez :

1. ✅ **Lancer l'entraînement** TRM depuis le frontend
2. ✅ **Suivre en temps réel** la progression (epochs, loss, sharpe)
3. ✅ **Lister les versions de modèles** avec leurs métadonnées
4. ✅ **Arrêter** un entraînement en cours
5. ✅ **Marquer** un modèle comme production
6. ✅ **Consulter les logs** d'entraînement

---

## 🚀 Démarrage Rapide

### 1. Démarrer le serveur backend

```bash
cd /Users/christopher/Desktop/futur/frontend_pipeline
python api_server.py
```

Le serveur démarre sur `http://localhost:8000`

### 2. Démarrer le frontend React

```bash
cd /Users/christopher/Desktop/futur/frontend_pipeline/frontend/alpha-dashboard
npm start
```

L'interface s'ouvre sur `http://localhost:3000`

### 3. Accéder à l'onglet Training

Dans le dashboard, cliquez sur l'onglet **🎓 Training** dans la navigation.

---

## 📊 Utilisation de l'Interface

### Démarrer un Nouvel Entraînement

1. **Sélectionner une configuration** :
   - `train_s3_tiny.yaml` - Ultra-léger (3 mois, 1 symbole)
   - `train_s3_light.yaml` - Optimisé 8GB RAM (1 an, 1 symbole)
   - `train_s3_optimized.yaml` - Équilibré 16GB RAM (2 ans, 3 symboles)
   - `train_s3.yaml` - Production complète (5 ans, 8 symboles)

2. **Choisir le device** :
   - `Auto` (recommandé) - Sélectionne automatiquement MPS ou CPU
   - `MPS` - Pour Apple Silicon (M1/M2/M3)
   - `CPU` - Pour processeurs classiques

3. **Debug Mode** (optionnel) :
   - Active un run rapide d'une époque pour tester

4. **Cliquer sur "Start Training"**

### Monitorer l'Entraînement

La section "Active Training Jobs" affiche :

- **Job ID** - Identifiant unique du job
- **Status** - État actuel (RUNNING, COMPLETED, FAILED, STOPPED)
- **Progress** - Barre de progression visuelle
- **Metrics** - Train Loss, Val Loss, Val Sharpe en temps réel
- **Temps écoulé** - Durée depuis le démarrage

**Actions disponibles** :
- **View Logs** - Afficher les 50 dernières lignes de logs
- **Stop Training** - Arrêter l'entraînement en cours

### Gérer les Versions de Modèles

La section "Model Versions" liste tous les modèles entraînés :

- **Filename** - Nom du fichier checkpoint
- **Size** - Taille du fichier en MB
- **Created** - Date de création
- **Config** - Configuration utilisée
- **Metrics** - Epochs, Loss, Val Loss, Sharpe
- **Production Badge** - Indique le modèle en production

**Actions disponibles** :
- **Set as Production** - Marquer un modèle comme production
- **Filtres** - All, Production, Completed, Failed

---

## 🔧 Architecture Technique

### Backend API Endpoints

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/training/configs` | GET | Liste des configs disponibles |
| `/training/start` | POST | Démarre un entraînement |
| `/training/jobs` | GET | Liste tous les jobs (24h) |
| `/training/status/{job_id}` | GET | Status d'un job spécifique |
| `/training/stop/{job_id}` | POST | Arrête un job |
| `/training/logs/{job_id}` | GET | Logs d'un job |
| `/training/models` | GET | Liste les versions de modèles |
| `/training/models/{filename}/set-production` | POST | Définit un modèle en production |
| `/training/models/{filename}/metadata` | GET | Métadonnées d'un modèle |

### Composants Frontend

| Composant | Fichier | Description |
|-----------|---------|-------------|
| `TrainingDashboard` | `TrainingDashboard.tsx` | Composant principal |
| `TrainingControl` | `TrainingControl.tsx` | Formulaire de démarrage |
| `TrainingMonitor` | `TrainingMonitor.tsx` | Monitoring des jobs actifs |
| `ModelVersions` | `ModelVersions.tsx` | Liste des modèles |

### Gestion des Jobs

Les jobs d'entraînement sont stockés en mémoire dans le serveur backend :

```python
training_jobs = {
    "train_20251215_143022": {
        "job_id": "train_20251215_143022",
        "status": "running",
        "config_path": "ai/configs/train_s3_light.yaml",
        "device": "mps",
        "current_epoch": 5,
        "total_epochs": 50,
        "progress_pct": 10.0,
        "current_loss": 0.452,
        "current_val_loss": 0.389,
        "current_sharpe": 1.234,
        "log_file": "/tmp/training_train_20251215_143022.log"
    }
}
```

### Monitoring en Temps Réel

Un thread de monitoring parse le fichier log toutes les 2 secondes :

```python
# Pattern recherché dans les logs :
# Epoch 5/50 | Train Loss: 0.452 | Val Loss: 0.389 | Val Sharpe: 1.234 | LR: 1.00e-04
```

Le frontend poll l'API toutes les 5 secondes pour rafraîchir l'interface.

### Métadonnées de Modèles

Après chaque entraînement, un fichier JSON est créé :

```json
{
  "job_id": "train_20251215_143022",
  "status": "completed",
  "config_path": "ai/configs/train_s3_light.yaml",
  "device": "mps",
  "current_epoch": 50,
  "total_epochs": 50,
  "current_loss": 0.234,
  "current_val_loss": 0.189,
  "current_sharpe": 1.567,
  "start_time": "2025-12-15T14:30:22",
  "end_time": "2025-12-15T15:15:45"
}
```

---

## 🎨 Design System

L'interface utilise un **design dark theme** avec **glass morphism** :

### Couleurs Principales

- **Primary Blue** - `#3B82F6` - Actions principales
- **Secondary Purple** - `#8B5CF6` - Accents
- **Success Green** - `#10B981` - Statut completed
- **Warning Orange** - `#F59E0B` - Debug mode, production
- **Error Red** - `#EF4444` - Statut failed

### Status Badge Colors

- **Running** - Bleu pulsant (`#3B82F6`)
- **Completed** - Vert (`#10B981`)
- **Failed** - Rouge (`#EF4444`)
- **Stopped** - Orange (`#F59E0B`)

---

## 📝 Exemples de Scénarios

### Scénario 1 : Entraînement Rapide de Test

```
1. Sélectionner : train_s3_tiny.yaml
2. Device : Auto
3. Activer : Debug Mode ✓
4. Start Training
5. Observer : Progression en temps réel
6. Résultat : Complété en ~2-3 minutes
```

### Scénario 2 : Entraînement Production Complet

```
1. Sélectionner : train_s3.yaml
2. Device : Auto (ou MPS si disponible)
3. Debug Mode : Désactivé
4. Start Training
5. Observer : Monitoring sur plusieurs heures
6. À la fin : Modèle disponible dans Model Versions
7. Action : Set as Production
```

### Scénario 3 : Comparer Plusieurs Configurations

```
1. Lancer train_s3_light.yaml
2. Lancer train_s3_optimized.yaml (en parallèle)
3. Observer les 2 jobs en temps réel
4. Comparer les métriques (Sharpe ratio)
5. Sélectionner le meilleur modèle
6. Set as Production
```

---

## 🐛 Dépannage

### Le serveur backend ne démarre pas

```bash
# Vérifier les dépendances
pip install fastapi uvicorn pydantic pymongo

# Vérifier les ports
lsof -i :8000
```

### L'entraînement ne démarre pas

1. Vérifier que le fichier de config existe : `ai/configs/train_s3_light.yaml`
2. Vérifier les logs du serveur backend
3. Vérifier que Python 3 est installé

### Les logs ne s'affichent pas

1. Vérifier que le fichier log existe : `/tmp/training_*.log`
2. Vérifier les permissions d'écriture sur `/tmp`
3. Recharger l'interface

### Le modèle ne s'affiche pas dans Model Versions

1. Attendre que l'entraînement soit terminé
2. Vérifier que le checkpoint existe : `ai/checkpoints_light/model_*.pt`
3. Rafraîchir la page

---

## 🔐 Sécurité

### Validations Backend

- ✅ Validation des noms de fichiers (évite path traversal)
- ✅ Limitation des chemins accessibles
- ✅ Pas d'exécution de code arbitraire
- ✅ Timeout sur les requêtes

### Gestion des Processus

- ✅ Graceful shutdown (SIGTERM puis SIGKILL)
- ✅ Nettoyage des processus zombies
- ✅ Limitation à 2 entraînements simultanés (recommandé)

---

## 📈 Métriques Suivies

| Métrique | Description |
|----------|-------------|
| **Train Loss** | Perte sur données d'entraînement |
| **Val Loss** | Perte sur données de validation |
| **Val Sharpe** | Sharpe ratio sur validation (métrique principale) |
| **Epochs** | Nombre d'époques complétées |
| **Progress %** | Pourcentage de complétion |
| **Time** | Temps écoulé depuis le démarrage |

---

## 🚀 Prochaines Améliorations Possibles

1. **WebSocket** pour streaming des logs en temps réel
2. **Graphiques** de progression (loss curves)
3. **Comparaison** side-by-side de modèles
4. **Download** des checkpoints depuis l'interface
5. **Historique** d'entraînement (plus de 24h)
6. **Notifications** push quand entraînement terminé
7. **Resume training** depuis un checkpoint
8. **Hyperparameter tuning** automatique

---

## 📚 Ressources

- **Backend API** : `/Users/christopher/Desktop/futur/frontend_pipeline/api_server.py`
- **Frontend** : `/Users/christopher/Desktop/futur/frontend_pipeline/frontend/alpha-dashboard/src/components/TrainingDashboard.tsx`
- **Styles** : `/Users/christopher/Desktop/futur/frontend_pipeline/frontend/alpha-dashboard/src/components/TrainingDashboard.css`
- **Configuration Training** : `/Users/christopher/Desktop/futur/ai/configs/`
- **Checkpoints** : `/Users/christopher/Desktop/futur/ai/checkpoints_light/`

---

## ✅ Checklist de Vérification

Avant de commencer :

- [ ] Backend API en cours d'exécution (`python api_server.py`)
- [ ] Frontend React en cours d'exécution (`npm start`)
- [ ] Configurations disponibles dans `ai/configs/`
- [ ] Dossier `ai/checkpoints_light/` existe
- [ ] Permissions d'écriture sur `/tmp`

Bon entraînement ! 🎓🤖
