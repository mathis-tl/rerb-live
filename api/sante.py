"""Vérification de vie de la fonction Vercel `/sante` : renvoie {"ok": true}. Sans stockage, sans clé, sans réseau."""
import json
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def _refuser(self):
        octets = json.dumps({"erreur": "méthode non autorisée"}).encode("utf-8")
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(octets)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(octets)

    do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuser

    def do_GET(self):
        octets = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(octets)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(octets)
