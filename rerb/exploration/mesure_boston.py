"""Erreur en mètres à Boston (niveau 1 et repères 0a, 0b) sur les relevés de l'enregistreur (LECTURE SEULE, aucun appel API).

Usage : python3 -I mesure_boston.py [AAAA-MM-JJ ...] [--decalage=SECONDES]
(défaut : tous les jours de ~/rerb-data/mbta_vehicles ; décalage de l'instant du GPS, test du biais, revue R1.)

Chaîne commune : exploration/pipeline.py. Ici : lecture des relevés MBTA, mémoire M2 (S1), quai remplacé par sa
gare parent (S1 bis), PK de la gare pris sur le quai le plus proche de la voie.
"""
import base64
import collections
import gzip
import json
import sys
from datetime import datetime
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))
sys.path.insert(0, str(RACINE / "rerb" / "exploration"))

from mbta import consolider_releve, lire_gps_mbta  # noqa: E402
from memoire import Memoire  # noqa: E402
from pipeline import afficher, mesurer_flux  # noqa: E402
from voie import (charger_arrets, charger_formes, charger_parents, charger_trips,  # noqa: E402
                  coordonnees_sur_voie)

DONNEES = Path.home() / "rerb-data"
STATIQUE = RACINE / "data" / "static" / "mbta"


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


def compter(jours, decalage=0.0):
    trips = charger_trips(STATIQUE / "trips.txt")
    arrets = charger_arrets(STATIQUE / "stops.txt")
    parents = charger_parents(STATIQUE / "stops.txt")
    quais = collections.defaultdict(list)  # gare parent -> coordonnées de ses quais
    for quai, gare in parents.items():
        if quai in arrets:
            quais[gare].append(arrets[quai])
    formes = charger_formes(STATIQUE / "shapes.txt", set(trips.values()))
    memoire = Memoire()
    previsions = ((recu, trains) for recu, trains in
                  (consolider_releve(corps, recu, memoire, parents) for recu, corps in releves("mbta_predictions", jours)))
    points = ((recu, lire_gps_mbta(corps, recu)) for recu, corps in releves("mbta_vehicles", jours))

    def coords_gare(voie, g):
        return (quais[g] and coordonnees_sur_voie(voie, quais[g])) or arrets[g]

    return mesurer_flux(points, previsions, trips.get, arrets, formes, coords_gare, decalage=decalage)


if __name__ == "__main__":
    args = sys.argv[1:]
    decalage = next((float(a.split("=")[1]) for a in args if a.startswith("--decalage=")), 0.0)
    jours = [a for a in args if not a.startswith("--")] or sorted(p.name for p in (DONNEES / "mbta_vehicles").iterdir())
    c, erreurs, info = compter(jours, decalage)
    afficher(c, erreurs, info, f"jours : {', '.join(jours)} ; décalage de l'instant du GPS : {decalage:g} s")
