"""Suivi texte d'un train à partir des relevés PRIM de l'enregistreur (LECTURE SEULE, aucun appel API).

Usage : python3 -I suivi_train.py <nom_du_train> <heure_min_UTC> <heure_max_UTC> <fin_UTC> [secondes]
Exemple : suivi_train.py ERIO37 11:50 12:30 13:15 60

Le train est repéré par son nom (VehicleJourneyName) et par l'heure de sa 1re gare de la liste
comprise entre heure_min et heure_max (UTC), car un même nom revient plusieurs fois par jour.
Toutes les `secondes`, on relit le DERNIER relevé et on estime la position à l'instant présent
(fonction estimer de rerb/position.py). Exploration : pas de tests propres, la logique est testée dans
test_position.py.
"""
import base64
import gzip
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))

from geometry import Geometry, load_morceaux  # noqa: E402
from position import Etat, Reseau, distance_depuis_reseau, estimer, lire_train  # noqa: E402
from stops import load_stops, stop_area_number  # noqa: E402

DONNEES = Path.home() / "rerb-data" / "prim_estimated_timetable"
CSV = RACINE / "rerb" / "donnees" / "arrets_rerb_C01743.csv"
TRACE = RACINE / "data" / "static" / "rerb" / "rerb_trace_C01743.json"


def dernier_releve():
    """(ts_recv_utc, liste de trains bruts) du relevé le plus récent lisible, ou None."""
    for jour in sorted(DONNEES.iterdir(), reverse=True):
        for f in sorted(jour.glob("*.jsonl.gz"), reverse=True):
            lignes = []
            try:
                with gzip.open(f, "rt") as fh:
                    for l in fh:
                        lignes.append(l)
            except (EOFError, OSError):
                pass  # fichier en cours d'écriture : on garde ce qui a été lu
            for l in reversed(lignes):
                try:
                    r = json.loads(l)
                    if str(r["status"]) != "200":
                        continue
                    corps = base64.b64decode(r["body_b64"])
                    try:
                        corps = gzip.decompress(corps)
                    except OSError:
                        pass
                    d = json.loads(corps)
                    trains = d["Siri"]["ServiceDelivery"]["EstimatedTimetableDelivery"][0][
                        "EstimatedJourneyVersionFrame"][0]["EstimatedVehicleJourney"]
                    return r["ts_recv_utc"], trains
                except (ValueError, KeyError):
                    continue
    return None


def hms_utc(texte):
    return datetime.fromisoformat(texte.replace("Z", "+00:00")).strftime("%H:%M:%S")


def choisir(trains, nom, h_min, h_max):
    for ej in trains:
        noms = [n.get("value") for n in (ej.get("VehicleJourneyName") or [])]
        if nom not in noms:
            continue
        c = ej["EstimatedCalls"]
        c = c["EstimatedCall"] if isinstance(c, dict) else c
        heures = sorted(x.get("ExpectedArrivalTime") or x.get("ExpectedDepartureTime")
                        or x.get("AimedArrivalTime") or x.get("AimedDepartureTime") or "~" for x in c)
        if h_min <= heures[0][11:16] <= h_max:
            return ej
    return None


def main():
    nom, h_min, h_max, fin = sys.argv[1:5]
    pas = int(sys.argv[5]) if len(sys.argv) > 5 else 60
    stops = load_stops(CSV)
    g = Geometry(load_morceaux(TRACE), {n: (s.lat, s.lon) for n, s in stops.items()})
    reseau = Reseau({p: g.distance(*p) for p in g.neighbor_pairs()})
    distance = distance_depuis_reseau(reseau)
    nom_gare = lambda ref: stops[stop_area_number(ref)].name  # noqa: E731
    print(f"suivi de {nom} (1re gare entre {h_min} et {h_max} UTC), fin à {fin} UTC, pas {pas} s", flush=True)
    while True:
        maintenant = datetime.now(timezone.utc)
        if maintenant.strftime("%H:%M") >= fin:
            print("heure de fin atteinte", flush=True)
            return
        rel = dernier_releve()
        if rel is None:
            print(maintenant.strftime("%H:%M:%S"), "aucun relevé lisible", flush=True)
        else:
            ts, trains = rel
            ej = choisir(trains, nom, h_min, h_max)
            tete = f"{maintenant.strftime('%H:%M:%S')} relevé {ts[11:19]}"
            if ej is None:
                print(tete, f"| {nom} absent du relevé", flush=True)
            else:
                train = lire_train(ej)
                e = estimer(train, maintenant, distance)
                age = int((maintenant - train.recorded_at).total_seconds() / 60)
                p = " PÉRIMÉ" if e.perime else ""
                if e.etat == Etat.A_QUAI:
                    pos = f"à quai : {nom_gare(e.gare)}"
                elif e.etat == Etat.EN_ROUTE:
                    pos = (f"en route {nom_gare(e.gare)} -> {nom_gare(e.gare_suivante)}, "
                           f"f={e.fraction:.2f}, {e.distance_m:.0f} m parcourus")
                else:
                    pos = f"{e.etat.value} ({e.motif or ''})"
                print(tete, f"| mise à jour train il y a {age} min{p} | {pos}", flush=True)
                if e.etat == Etat.TERMINE:
                    print("train terminé", flush=True)
                    return
        time.sleep(pas)


if __name__ == "__main__":
    main()
