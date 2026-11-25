"""Fonction Vercel `/quota` (T6a, diagnostic) : routage HTTP seulement ; la logique est dans rerb/prod.py.
Répond seulement si RERB_DIAGNOSTIC=1 ; sinon 404.
# INVARIANT : cette route n'appelle jamais PRIM et n'écrit rien (lecture d'un MGET)."""
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rerb"))

import prod  # noqa: E402

_services = None   # une copie de fonction garde sa configuration entre deux requêtes


def services():
    global _services
    if _services is None:
        _services = prod.Services(os.environ)
    return _services


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        code, corps = prod.reponse_quota(services(), os.environ.get("RERB_DIAGNOSTIC") == "1")
        prod.envoyer(self, code, corps, prod.CACHE_SANS_CACHE)
