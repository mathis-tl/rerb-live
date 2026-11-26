#!/usr/bin/env bash
# Vérification rapide du projet : tests de la logique (bibliothèque standard seulement).
set -euo pipefail
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
(cd "$RACINE/rerb" && python3 -m unittest discover -p "test_*.py")
