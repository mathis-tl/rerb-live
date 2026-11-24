"""Mesure de démarrage à froid (T4, pré-production). À SUPPRIMER en T5/T6 (remplacée).

Renvoie la version de Python, l'heure UTC et l'heure de Paris, le temps d'import de rerb/serveur.py et de
construire_contexte() (ms), et « froid » = premier appel de cette copie de fonction.
Ne lit aucun réglage de l'hôte, aucune clé, et ne fait aucun appel réseau.
"""
import json
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from zoneinfo import ZoneInfo

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "rerb"))   # serveur.py importe ses modules par leur nom simple

# INVARIANT : cette fonction ne lit aucun réglage de l'hôte et ne fait aucun appel réseau (pas de clé, pas de PRIM).
_froid = True   # une seule fois par copie de fonction


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _froid
        froid, _froid = _froid, False
        debut = time.perf_counter()
        import serveur   # noqa: E402  (import tardif : c'est ce qu'on mesure)
        import_ms = (time.perf_counter() - debut) * 1000
        debut = time.perf_counter()
        serveur.construire_contexte()
        contexte_ms = (time.perf_counter() - debut) * 1000
        maintenant = datetime.now(timezone.utc)
        corps = {
            "ok": True,
            "python": sys.version.split()[0],
            "utc": maintenant.isoformat(timespec="seconds"),
            "paris": maintenant.astimezone(ZoneInfo("Europe/Paris")).isoformat(timespec="seconds"),
            "import_serveur_ms": round(import_ms, 1),
            "construire_contexte_ms": round(contexte_ms, 1),
            "froid": froid,
        }
        octets = json.dumps(corps, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(octets)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(octets)
