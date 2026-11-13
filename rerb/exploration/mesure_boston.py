"""Comptes de l'appariement prévision-GPS à Boston sur les relevés de l'enregistreur (LECTURE SEULE, aucun appel API).

Usage : python3 -I mesure_boston.py [AAAA-MM-JJ ...]      (défaut : tous les jours de ~/rerb-data/mbta_vehicles)

Pour chaque point GPS (mbta_vehicles), `apparier` (rerb/mbta.py) cherche le dernier relevé de prévisions reçu
au plus tard à l'instant du GPS (E2a). Résultat : nombre de points appariés et écartés par motif (E4).
Pas encore d'erreur en mètres (suite : distance sur la voie, voie.py).

Mémoire : seuls les relevés de prévisions des FENETRE dernières secondes avant chaque relevé GPS sont gardés.
Un GPS périmé (> 120 s) est écarté avant toute recherche, donc 300 s suffisent. Logique testée dans test_mbta.py.
"""
import base64
import collections
import gzip
import json
import sys
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))

from mbta import apparier, grouper_par_course, lire_gps_mbta  # noqa: E402

DONNEES = Path.home() / "rerb-data"
FENETRE = timedelta(seconds=300)


def releves(flux, jours):
    """(ts_recv_utc, corps JSON:API décodé) en ordre croissant, relevés en erreur ignorés."""
    for jour in jours:
        for f in sorted((DONNEES / flux / jour).glob("*.jsonl.gz")):
            with gzip.open(f, "rt") as fh:
                for ligne in fh:
                    r = json.loads(ligne)
                    if r.get("error") or not r.get("body_b64"):
                        continue
                    yield datetime.fromisoformat(r["ts_recv_utc"]), json.loads(base64.b64decode(r["body_b64"]))


def compter(jours):
    prev = releves("mbta_predictions", jours)
    suivant = next(prev, None)
    fenetre = deque()
    c = collections.Counter()
    for recu, corps in releves("mbta_vehicles", jours):
        while suivant is not None and suivant[0] <= recu:
            fenetre.append((suivant[0], grouper_par_course(suivant[1])))
            suivant = next(prev, None)
        while fenetre and fenetre[0][0] < recu - FENETRE:
            fenetre.popleft()
        liste = list(fenetre)
        for p in lire_gps_mbta(corps, recu):
            c["points GPS"] += 1
            train, motif = apparier(p, liste)
            c["appariés" if motif is None else f"écarté : {motif}"] += 1
    return c


if __name__ == "__main__":
    jours = sys.argv[1:] or sorted(p.name for p in (DONNEES / "mbta_vehicles").iterdir())
    c = compter(jours)
    total = c["points GPS"]
    print(f"jours : {', '.join(jours)}")
    for k, n in c.most_common():
        print(f"{k:55s} {n:8d}  {100 * n / total:5.1f} %")
