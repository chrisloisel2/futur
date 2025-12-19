# 🚀 Quick Start - Lancement Rapide sur le Serveur

**3 commandes pour démarrer l'entraînement corrigé.**

---

## Étape 1: Vérifier ✅

```bash
python3 ai/verify_correction.py
```

**Attendu:**
```
✅ ALL CORRECTIONS VERIFIED!
```

---

## Étape 2: Nettoyer 🧹

```bash
./cleanup_server_windows.sh
```

**Confirmer avec `Y`**

**Ou manuellement:**
```bash
rm -rf training_output_corrected/
```

---

## Étape 3: Lancer 🎯

```bash
python3 ai/train_advanced.py --config ai/configs/train_corrected.yaml
```

---

## Vérifications Rapides

### ✅ Étape 4 (Dataset Building) doit montrer:

```
Dataset created: <_PrefetchDataset element_spec=(
  ...,
  'rv': TensorSpec(shape=(128,), dtype=tf.float32, ...)
)>
```

**⚠️ Si vous voyez `(128, 12)` au lieu de `(128,)` → Anciens NPZ pas supprimés!**

### ✅ Training doit démarrer:

```
EPOCH 1/20
Epoch 1/20
1/500 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### ✅ Epoch 1 doit montrer:

```
dir_accuracy >= 0.53
Pas de ValueError
Losses stables
```

---

## En Cas d'Erreur

### ValueError dimension mismatch:

```bash
rm -rf training_output_corrected/
# Relancer étape 3
```

### Dataset montre rv: (128, 12):

```bash
# Vérifier correction
grep "rv.*TensorSpec" ai/data_pipeline_memory_efficient.py
# Doit montrer: shape=()
```

---

## Monitoring

### TensorBoard (dans un autre terminal):

```bash
tensorboard --logdir=training_output_corrected/tensorboard/ --port=6006
```

Ouvrir: http://localhost:6006

### Logs:

```bash
tail -f training_output_corrected/logs/train_advanced.log
```

---

## Documentation Complète

- **[README_CORRECTION_FINALE.md](README_CORRECTION_FINALE.md)** - Guide complet
- **[SOLUTION_FINALE.md](SOLUTION_FINALE.md)** - Documentation détaillée
- **[FINAL_FIX_SUMMARY.md](FINAL_FIX_SUMMARY.md)** - Résumé technique

---

**Bon entraînement!** 🎉
