#!/usr/bin/env bash
# Vérification rapide du projet (appelée par le hook Stop global).
# Lance les tests de l'enregistreur si son venv existe, sinon no-op.
set -euo pipefail
cd "$(dirname "$0")/../recorder" 2>/dev/null || exit 0
[ -x .venv/bin/python ] || exit 0
.venv/bin/python -m unittest test_recorder
