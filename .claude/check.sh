#!/usr/bin/env bash
# Vérification rapide du projet (appelée par le hook Stop global).
# 1) tests de la logique RER B (rerb/, bibliothèque standard seulement)
# 2) tests de l'enregistreur si son venv existe
set -euo pipefail
RACINE="$(cd "$(dirname "$0")/.." && pwd)"

if [ -d "$RACINE/rerb" ]; then
  (cd "$RACINE/rerb" && python3 -m unittest discover -p "test_*.py")
fi

if [ -x "$RACINE/recorder/.venv/bin/python" ]; then
  (cd "$RACINE/recorder" && .venv/bin/python -m unittest test_recorder)
fi
