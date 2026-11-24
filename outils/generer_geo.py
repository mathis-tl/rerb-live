"""Produit web/data/geo.json (tracé et gares de la carte) avec rerb/serveur.py:charger_geo().

Usage : python3 outils/generer_geo.py
À relancer si les données statiques de la ligne changent. rerb/test_deploiement.py vérifie que le fichier est à jour
(octet pour octet, même sérialisation que la route /geo du serveur local).
"""
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "rerb"))

from serveur import charger_geo  # noqa: E402


def main():
    octets = json.dumps(charger_geo(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sortie = RACINE / "web" / "data" / "geo.json"
    sortie.write_bytes(octets)
    print(f"{sortie.relative_to(RACINE)} : {len(octets)} octets")


if __name__ == "__main__":
    main()
