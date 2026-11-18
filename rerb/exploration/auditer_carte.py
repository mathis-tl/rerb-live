"""Audit de la carte en direct (lecture seule) : interroge `/trains` du serveur LOCAL toutes les PAS secondes et signale les anomalies.

Usage : python3 -I rerb/exploration/auditer_carte.py [--url http://127.0.0.1:8765/trains] [--duree 600] [--pas 3] [--json SORTIE.json]

Aucun appel PRIM : le serveur ne rappelle PRIM qu'à son propre rythme. Seules les adresses 127.0.0.1 / localhost sont acceptées.
Logique dans rerb/audit_carte.py (testée). Ce script ne fait que lire, attendre et imprimer.
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from audit_carte import auditer, url_autorisee  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8765/trains")
    p.add_argument("--duree", type=int, default=600, help="secondes de surveillance")
    p.add_argument("--pas", type=float, default=3.0)
    p.add_argument("--json", type=Path)
    a = p.parse_args()
    if not url_autorisee(a.url):
        sys.exit("adresse refusée : seul le serveur local (127.0.0.1 ou localhost, http) est autorisé")
    serie, t0 = [], time.monotonic()
    while time.monotonic() - t0 <= a.duree:
        with urllib.request.urlopen(a.url, timeout=10) as r:
            serie.append((time.monotonic() - t0, json.load(r)["trains"]))
        time.sleep(a.pas)
    res = auditer(serie)
    print(f"{len(serie)} relevés sur {a.duree} s ; trains vus : {len({x['course'] for _, ts in serie for x in ts})}")
    for nom, lignes in res.items():
        print(f"\n{nom} : {len(lignes)}")
        for l in lignes[:20]:
            print("   ", {k: (round(v, 1) if isinstance(v, float) else v) for k, v in l.items()})
    if a.json:
        a.json.write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
