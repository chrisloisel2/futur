#!/usr/bin/env bash
# scripts/freeze_repo.sh
# ─────────────────────────────────────────────────────────────────────────────
# Étape 0 du refactor "portfolio operating system" : geler l'état du dépôt
# AVANT le moindre refactor. Zéro perte possible, zéro ambiguïté historique.
#
# Crée :
#   - branche freeze/<date>-state    (snapshot git de l'état courant)
#   - tag annoté v0.6-...            (point de restauration nommé)
#   - bundle git ../futur_*.bundle   (sauvegarde de tout l'historique git)
#   - tar ../futur_artifacts_*.tar.gz (artifacts/ untracked, modèles/datasets)
#   - branche feat/portfolio-os      (branche de travail pour le refactor)
#
# Idempotent-ish : refuse d'écraser une branche/tag existante.
set -euo pipefail

DATE="${1:-$(date +%Y-%m-%d)}"
FREEZE_BRANCH="freeze/${DATE}-state"
WORK_BRANCH="feat/portfolio-os"
TAG="v0.6-trm-paper-institutional-${DATE}"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

echo "==> Freeze repo @ ${DATE} (root=${ROOT})"

mkdir -p reports
git status --short > "reports/repo_state_${DATE}.txt"
echo "==> repo state written to reports/repo_state_${DATE}.txt"

# 1. branche de freeze + commit snapshot
if git show-ref --verify --quiet "refs/heads/${FREEZE_BRANCH}"; then
  echo "!! freeze branch ${FREEZE_BRANCH} existe déjà, on saute la création"
  git switch "${FREEZE_BRANCH}"
else
  git switch -c "${FREEZE_BRANCH}"
fi
git add -A
git commit -m "freeze: trm v5 paper + institutional engine cleanup state ${DATE}" || \
  echo "   (rien à committer — working tree propre)"

# 2. tag annoté
if git rev-parse "${TAG}" >/dev/null 2>&1; then
  echo "!! tag ${TAG} existe déjà, on saute"
else
  git tag -a "${TAG}" -m "TRM v5 paper + institutional bootstrap (${DATE})"
fi

# 3. bundle git (tout l'historique)
BUNDLE="../futur_${TAG}.bundle"
git bundle create "${BUNDLE}" --all
echo "==> bundle git => ${BUNDLE}"

# 4. tar des artefacts untracked (modèles, datasets, états paper)
TARBALL="../futur_artifacts_${DATE}.tar.gz"
tar -czf "${TARBALL}" artifacts/ configs/ reports/ 2>/dev/null || true
echo "==> tar artefacts => ${TARBALL}"

# 5. branche de travail
if git show-ref --verify --quiet "refs/heads/${WORK_BRANCH}"; then
  git switch "${WORK_BRANCH}"
else
  git switch -c "${WORK_BRANCH}"
fi

echo "==> Freeze terminé. Branche de travail = ${WORK_BRANCH}"
git log --oneline -1
