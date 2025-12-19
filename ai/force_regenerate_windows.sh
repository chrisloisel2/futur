#!/bin/bash
#
# Force la régénération des windows avec la nouvelle structure
# À exécuter sur le serveur distant
#

set -e

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Régénération Forcée des Windows (Nouvelle Structure)         ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Supprimer TOUS les anciens outputs
echo "🧹 Suppression des anciens fichiers..."

dirs_to_remove=(
    "training_output"
    "training_output_returns_only"
    "training_output_corrected"
)

for dir in "${dirs_to_remove[@]}"; do
    if [ -d "$dir" ]; then
        echo "   ❌ Suppression: $dir/"
        rm -rf "$dir"
    fi
done

echo "✅ Nettoyage terminé"
echo ""

# Vérifier que model.py a les bonnes modifications
echo "🔍 Vérification de model.py..."

if grep -q "y_rv_agg = np.zeros((N,), dtype=np.float32)" ai/models/model.py; then
    echo "   ✅ make_windows() corrigé (RV scalaire)"
else
    echo "   ❌ ERREUR: make_windows() n'est pas corrigé!"
    echo "   Vérifier que model.py contient:"
    echo "     y_rv_agg = np.zeros((N,), dtype=np.float32)"
    echo "     y_rv_agg[idx] = float(np.sqrt(np.mean(fut_rv ** 2)))"
    exit 1
fi

if grep -q "y_dir\[idx\] = 1 if cum >= 0.0 else 0" ai/models/model.py; then
    echo "   ✅ Direction binaire corrigée"
else
    echo "   ❌ ERREUR: Direction n'est pas binaire!"
    exit 1
fi

if grep -q "Dense(2)" ai/models/model.py; then
    echo "   ✅ Direction head = 2 classes"
else
    echo "   ⚠️  WARNING: Direction head peut-être pas mis à jour"
fi

if grep -q "Dense(1).*# CHANGED: Scalar output" ai/models/model.py; then
    echo "   ✅ RV head = scalaire"
else
    echo "   ⚠️  WARNING: RV head peut-être pas mis à jour"
fi

echo ""
echo "✅ Vérifications passées"
echo ""

# Lancer l'entraînement
CONFIG="${1:-ai/configs/train_corrected.yaml}"

if [ ! -f "$CONFIG" ]; then
    echo "❌ Config non trouvée: $CONFIG"
    exit 1
fi

echo "📄 Configuration: $CONFIG"
echo ""
echo "🚀 Lancement de l'entraînement..."
echo "   (Les windows seront régénérées automatiquement)"
echo ""

python3 ai/train_advanced.py --config "$CONFIG"

exit_code=$?

if [ $exit_code -eq 0 ]; then
    echo ""
    echo "✅ Entraînement terminé avec succès!"
else
    echo ""
    echo "❌ Entraînement échoué (code: $exit_code)"
fi

exit $exit_code
