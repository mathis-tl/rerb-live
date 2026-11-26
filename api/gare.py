"""Fonction Vercel `/gare?ref=<numéro>` : routage HTTP seulement ; la logique est dans rerb/prod.py."""
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rerb"))

import prod  # noqa: E402

_services = None


def services():
    global _services
    if _services is None:
        _services = prod.Services(os.environ)
    return _services


class handler(BaseHTTPRequestHandler):
    def _refuser(self):
        prod.methode_refusee(self)

    do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuser

    def do_GET(self):
        svc = services()
        ref = prod.ref_gare(self.path, svc.gares)
        if ref is None:   # hors liste blanche : aucune requête PRIM, aucune lecture du stockage, pas de cache
            prod.envoyer(self, 404, {"erreur": "gare absente de la liste blanche"}, prod.CACHE_SANS_CACHE)
            return
        prod.envoyer(self, 200, prod.reponse_gare(svc, ref), prod.CACHE_GARE)
