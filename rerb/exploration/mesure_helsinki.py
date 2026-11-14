"""Erreur en mètres à Helsinki (niveau 1 et repères 0a, 0b) sur les relevés de l'enregistreur (LECTURE SEULE, aucun appel API).

Usage : recorder/.venv/bin/python -I rerb/exploration/mesure_helsinki.py [AAAA-MM-JJ ...] [--decalage=SECONDES] [--pas=SECONDES]
(interpréteur avec protobuf requis ; défaut : tous les jours de ~/rerb-data/hsl_trip_updates ; pas = 10 s.)

Prévisions : `hsl_trip_updates` (GTFS-RT, protobuf) décodées en dictionnaires puis `rerb/hsl.py`.
GPS : `hsl_mqtt_trains` (MQTT HFP, un message par seconde et par train), instant = `tst` (S4), un point toutes les
`pas` secondes par véhicule (le flux standard `hsl_witness_vehicle_positions` n'a qu'un relevé par heure).
Jointure par (ligne, sens, jour, heure de départ) : le trip_id du temps réel est vide. Boucles : P2 (1re gare,
premier passage). Chaîne commune : exploration/pipeline.py.
"""
import base64
import collections
import csv
import gzip
import json
import re
import sys
from datetime import datetime
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))
sys.path.insert(0, str(RACINE / "rerb" / "exploration"))

from google.transit import gtfs_realtime_pb2  # noqa: E402
from hsl import choisir_rame, cle_course, lire_point_hfp, lire_train_hsl  # noqa: E402
from pipeline import afficher, mesurer_flux  # noqa: E402
from voie import charger_arrets, charger_formes  # noqa: E402

DONNEES = Path.home() / "rerb-data"
STATIQUE = RACINE / "data" / "static" / "hsl"
TRIP, STOP = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship, gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship


DOUBLONS_CLE = [0]


def fichiers(flux, jours):
    for jour in jours:
        yield from sorted((DONNEES / flux / jour).glob("*.jsonl.gz"))


def previsions(jours):
    """(ts_recv_utc, {cle de course: Train}) en ordre croissant."""
    for f in fichiers("hsl_trip_updates", jours):
        with gzip.open(f, "rt") as fh:
            for ligne in fh:
                r = json.loads(ligne)
                if r.get("error") or not r.get("body_b64"):
                    continue
                octets = base64.b64decode(r["body_b64"])
                try:
                    octets = gzip.decompress(octets)
                except OSError:
                    pass
                flux = gtfs_realtime_pb2.FeedMessage()
                flux.ParseFromString(octets)
                recu = datetime.fromisoformat(r["ts_recv_utc"])
                trains = {}
                for e in flux.entity:
                    t = e.trip_update.trip
                    if not t.route_id.startswith("300"):
                        continue
                    maj = {
                        "route_id": t.route_id, "direction_id": t.direction_id, "start_date": t.start_date,
                        "start_time": t.start_time, "schedule_relationship": TRIP.Name(t.schedule_relationship),
                        "stop_time_updates": [{
                            "stop_id": u.stop_id,
                            "arrival": u.arrival.time if u.HasField("arrival") else None,
                            "departure": u.departure.time if u.HasField("departure") else None,
                            "schedule_relationship": STOP.Name(u.schedule_relationship)} for u in e.trip_update.stop_time_update]}

                    cle = cle_course(t.route_id, t.direction_id, t.start_date, t.start_time)
                    if cle in trains:
                        DOUBLONS_CLE[0] += 1  # revue R2 point 3 : deux entités de même clé dans un relevé, la dernière gagne
                    trains[cle] = lire_train_hsl(maj, recu)
                yield recu, trains


def points(jours, pas, amont):
    """(ts_recv_utc, [PointGps]) : un point par véhicule et par tranche de `pas` secondes, lignes 300x seulement.
    S4 option C : par clé de course et tranche, `choisir_rame` ; les écartés sont comptés dans `amont`."""
    if pas < 1:
        raise SystemExit("--pas doit être >= 1 s")
    tranche, groupe = None, {}

    def vider():
        for course, cands in groupe.items():
            if course is None:
                for _, p in cands:
                    yield p.recu, [p]
                continue
            point, motif, n = choisir_rame(cands)
            if motif:
                amont[motif] += n
            if point is not None:
                yield point.recu, [point]
        groupe.clear()

    for f in fichiers("hsl_mqtt_trains", jours):
        with gzip.open(f, "rt") as fh:
            for ligne in fh:
                r = json.loads(ligne)
                morceaux = r["topic"].split("/")
                if len(morceaux) < 10 or not morceaux[9].startswith("300"):
                    continue
                ts = r["ts_recv_utc"]
                t = (ts[:10], (int(ts[11:13]) * 3600 + int(ts[14:16]) * 60 + int(ts[17:19])) // int(pas))
                if t != tranche:
                    yield from vider()
                    tranche, vus = t, set()
                veh = (morceaux[7], morceaux[8])
                if veh in vus:
                    continue
                vus.add(veh)
                p = lire_point_hfp(json.loads(r["payload"]), datetime.fromisoformat(ts))
                if p.latitude is None or p.longitude is None:
                    continue
                groupe.setdefault(p.course, []).append(("".join(veh), p))
    yield from vider()


def compter(jours, decalage=0.0, pas=10.0):
    cle_shape = collections.defaultdict(set)
    with (STATIQUE / "trips.txt").open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            m = re.search(r"_(\d{4})$", r["trip_id"])
            if m:
                cle_shape[(r["route_id"], r["direction_id"], m.group(1))].add(r["shape_id"])

    def forme_de(course):
        s = cle_shape.get((course[0], str(course[1]), course[3]), set()) if course else set()
        return next(iter(s)) if len(s) == 1 else None

    arrets = charger_arrets(STATIQUE / "stops.txt")
    formes = charger_formes(STATIQUE / "shapes.txt", {s for v in cle_shape.values() for s in v})
    amont = collections.Counter()
    flux = points(jours, pas, amont)  # `amont` se remplit pendant la lecture, il est lu après
    c, erreurs, info = mesurer_flux(flux, previsions(jours), forme_de, arrets, formes,
                                    lambda voie, g: arrets[g], premier_passage=True, decalage=decalage, amont=amont)
    info["entités de prévision de même clé dans un relevé (la dernière gagne)"] = DOUBLONS_CLE[0]
    return c, erreurs, info


if __name__ == "__main__":
    args = sys.argv[1:]
    decalage = next((float(a.split("=")[1]) for a in args if a.startswith("--decalage=")), 0.0)
    pas = next((float(a.split("=")[1]) for a in args if a.startswith("--pas=")), 10.0)
    jours = [a for a in args if not a.startswith("--")] or sorted(p.name for p in (DONNEES / "hsl_trip_updates").iterdir())
    c, erreurs, info = compter(jours, decalage, pas)
    afficher(c, erreurs, info, f"jours : {', '.join(jours)} ; décalage {decalage:g} s ; un point par véhicule et par tranche de {pas:g} s")
