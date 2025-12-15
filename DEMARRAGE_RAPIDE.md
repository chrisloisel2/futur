# 🚀 Démarrage Rapide - Système de Training

## ⚠️ IMPORTANT : Redémarrer le Serveur API

Les nouveaux endpoints de training ont été ajoutés au code. **Vous devez redémarrer le serveur API** pour qu'ils soient disponibles.

### Étape 1 : Arrêter le serveur actuel

Si le serveur est déjà en cours d'exécution, arrêtez-le :

```bash
# Trouver le processus
lsof -i :8000

# Ou plus simplement, tuer tous les processus Python sur le port 8000
pkill -f "api_server.py"
```

### Étape 2 : Démarrer le nouveau serveur

```bash
cd /Users/christopher/Desktop/futur/frontend_pipeline
python api_server.py
```

Vous devriez voir :
```
🚀 ALPHA TRADING API SERVER
================================
Starting server on http://localhost:8000
```

### Étape 3 : Vérifier que les endpoints fonctionnent

Dans un autre terminal :

```bash
cd /Users/christopher/Desktop/futur/frontend_pipeline
./test_training_api.sh
```

Vous devriez voir des ✅ verts pour tous les tests.

### Étape 4 : Démarrer le frontend (si pas déjà lancé)

```bash
cd /Users/christopher/Desktop/futur/frontend_pipeline/frontend/alpha-dashboard
npm start
```

### Étape 5 : Accéder à l'interface

Ouvrez http://localhost:3000 et cliquez sur l'onglet **🎓 Training**.

---

## 🎯 Démarrage Automatique (Recommandé)

Le moyen le plus simple est d'utiliser le script automatique qui gère tout :

```bash
cd /Users/christopher/Desktop/futur/frontend_pipeline
./start_training_platform.sh
```

Ce script va :
1. ✅ Arrêter les anciens processus
2. ✅ Démarrer le backend avec les nouveaux endpoints
3. ✅ Démarrer le frontend React
4. ✅ Ouvrir le navigateur automatiquement
5. ✅ Afficher les logs en temps réel

Pour arrêter : **Ctrl+C**

---

## 🧪 Vérification Manuelle des Endpoints

### Test 1 : Lister les configurations
```bash
curl http://localhost:8000/training/configs | python -m json.tool
```

Résultat attendu :
```json
{
  "success": true,
  "configs": [
    "train_ccxt.yaml",
    "train_s3.yaml",
    "train_s3_light.yaml",
    "train_s3_optimized.yaml",
    "train_s3_tiny.yaml"
  ],
  "count": 5
}
```

### Test 2 : Lister les jobs d'entraînement
```bash
curl http://localhost:8000/training/jobs | python -m json.tool
```

Résultat attendu :
```json
{
  "success": true,
  "jobs": [],
  "count": 0
}
```

### Test 3 : Lister les modèles
```bash
curl http://localhost:8000/training/models | python -m json.tool
```

Résultat attendu :
```json
{
  "success": true,
  "models": [
    {
      "filename": "model_20251214_2116.pt",
      "size_mb": 15.24,
      "created_at": "2025-12-14T21:16:33",
      ...
    }
  ],
  "count": 2
}
```

---

## ❓ Dépannage

### Erreur "Failed to fetch training configs"

**Cause** : Le serveur API n'a pas les nouveaux endpoints.

**Solution** : Redémarrer le serveur API (voir Étape 1 ci-dessus).

### Erreur "Port 8000 already in use"

**Cause** : Un ancien serveur est encore en cours d'exécution.

**Solution** :
```bash
pkill -f "api_server.py"
# Attendre 2 secondes puis relancer
python api_server.py
```

### Erreur "Cannot GET /training/configs"

**Cause** : Vous accédez à la mauvaise URL.

**Solution** : Utilisez `http://localhost:8000/training/configs` (pas `http://localhost:3000`).

### Le frontend ne charge pas

**Cause** : Dependencies npm manquantes.

**Solution** :
```bash
cd /Users/christopher/Desktop/futur/frontend_pipeline/frontend/alpha-dashboard
npm install
npm start
```

---

## 📋 Checklist de Démarrage

- [ ] Serveur API redémarré avec le nouveau code
- [ ] Test des endpoints réussi (`./test_training_api.sh`)
- [ ] Frontend React lancé
- [ ] Navigateur ouvert sur http://localhost:3000
- [ ] Onglet "Training" visible dans la navigation
- [ ] Sélecteur de configuration affiche les fichiers .yaml

Si tous les points sont cochés ✅, vous êtes prêt à entraîner vos modèles !

---

## 🎓 Premier Test Rapide

1. Cliquez sur l'onglet **🎓 Training**
2. Sélectionnez **train_s3_tiny.yaml** (le plus rapide)
3. Device : **Auto**
4. Activez **Debug Mode** ✓
5. Cliquez sur **🚀 Start Training**
6. Observez la progression en temps réel !

L'entraînement devrait se terminer en 2-3 minutes avec le mode debug.

---

**Besoin d'aide ?** Consultez [TRAINING_SYSTEM_GUIDE.md](file:///Users/christopher/Desktop/futur/TRAINING_SYSTEM_GUIDE.md) pour la documentation complète.
