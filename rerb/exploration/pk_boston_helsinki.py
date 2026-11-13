"""Contrôle (lecture seule) : gares et GPS de Boston et Helsinki tombent-ils sur la voie de leur train ?

Lit le DERNIER relevé de prévisions et de GPS dans ~/rerb-data (aucun appel réseau) et compte, avec
rayon 150 m (décision P1) : gares projetées, gares hors voie, ambiguës, rapport distance sur voie /
ligne droite entre gares consécutives, GPS projetés / rejetés.
Usage : recorder/.venv/bin/python rerb/exploration/pk_boston_helsinki.py   (protobuf requis pour HSL)
"""
import base64
import glob
import gzip
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))

from geometry import _dist  # noqa: E402
from voie import (PkAmbigu, PointHorsVoie, Voie, charger_arrets, charger_formes,  # noqa: E402
                  charger_trips, pk_gares)

PREMIER_PASSAGE = True  # décision P2 (13/11/2026) : 1re gare ambiguë -> premier passage de la voie
H = Path.home() / "rerb-data"
S = RACINE / "data" / "static"


def dernier(flux):
    f = sorted(glob.glob(str(H / flux / "*" / "*")))[-1]
    for l in reversed(gzip.open(f, "rt").readlines()):
        r = json.loads(l)
        if str(r["status"]) != "200":
            continue
        c = base64.b64decode(r["body_b64"])
        try:
            c = gzip.decompress(c)
        except OSError:
            pass
        return r["ts_recv_utc"], c


def resume(nom, pas_gares, rapports, gps_ok, gps_rejets):
    print(f"\n== {nom}")
    print("gares :", dict(pas_gares))
    if rapports:
        q = statistics.quantiles(rapports, n=10) if len(rapports) > 9 else []
        print(f"rapport distance sur voie / ligne droite entre gares consécutives ({len(rapports)} couples) : "
              f"médiane {statistics.median(rapports):.2f}, min {min(rapports):.2f}, max {max(rapports):.2f}"
              + (f", 90 % sous {q[-1]:.2f}" if q else ""))
    print("GPS :", gps_ok, "projetés,", dict(gps_rejets), "rejetés")


def traiter(nom, trains, shape_de, arrets, formes, gps):
    """trains : {cle: [stop_id dans l'ordre]} ; gps : [(cle, lat, lon)]."""
    voies, compte, rapports = {}, Counter(), []
    for cle, ordre in trains.items():
        sid = shape_de(cle)
        if sid is None or sid not in formes:
            compte["train sans forme de voie"] += 1
            continue
        voie = voies.setdefault(sid, Voie(formes[sid]))
        gares = [(g, *arrets[g]) for g in ordre if g in arrets]
        compte["gares inconnues du fichier statique"] += len(ordre) - len(gares)
        try:
            pks = pk_gares(voie, gares, premier_passage=PREMIER_PASSAGE)  # P2 : actif pour HSL
        except PointHorsVoie:
            compte["train rejeté : une gare hors voie ou hors ordre"] += 1
            continue
        except PkAmbigu:
            compte["train rejeté : 1re gare ambiguë"] += 1
            compte[f"  dont ligne {cle[0] if isinstance(cle, tuple) else '?'}"] += 1
            continue
        compte["trains projetés"] += 1
        coords = {g: (la, lo) for g, la, lo in gares}
        for (a, pa), (b, pb) in zip(pks, pks[1:]):
            droite = _dist(coords[a], coords[b])
            if droite > 50:
                rapports.append((pb - pa) / droite)
    ok, rejets = 0, Counter()
    for cle, lat, lon in gps:
        sid = shape_de(cle)
        if sid is None or sid not in formes:
            rejets["train sans forme"] += 1
            continue
        voie = voies.setdefault(sid, Voie(formes[sid]))
        try:
            voie.pk(lat, lon)
            ok += 1
        except PointHorsVoie:
            rejets["hors voie (> 150 m)"] += 1
        except PkAmbigu:
            rejets["ambigu (boucle)"] += 1
    resume(nom, compte, rapports, ok, rejets)


def boston():
    trips = charger_trips(S / "mbta/trips.txt")
    arrets = charger_arrets(S / "mbta/stops.txt")
    ts, c = dernier("mbta_predictions")
    pred = json.loads(c)["data"]
    ordre = defaultdict(list)
    for p in pred:
        t = p["relationships"]["trip"]["data"]
        s = p["relationships"]["stop"]["data"]
        if t and s:
            ordre[t["id"]].append((p["attributes"]["stop_sequence"] or 0, s["id"]))
    trains = {t: [s for _, s in sorted(v)] for t, v in ordre.items()}
    ts2, c2 = dernier("mbta_vehicles")
    gps = []
    for v in json.loads(c2)["data"]:
        t = v["relationships"]["trip"]["data"]
        a = v["attributes"]
        if t and a["latitude"] is not None:
            gps.append((t["id"], a["latitude"], a["longitude"]))
    ids = {trips[t] for t in set(trains) | {g[0] for g in gps} if t in trips}
    formes = charger_formes(S / "mbta/shapes.txt", ids)
    print(f"Boston : prévisions du {ts}, GPS du {ts2}, {len(trains)} trains, {len(gps)} véhicules")
    traiter("Boston (MBTA)", trains, lambda t: trips.get(t), arrets, formes, gps)


def helsinki():
    from google.transit import gtfs_realtime_pb2
    import csv
    import re
    cle_shape = defaultdict(set)
    with open(S / "hsl/trips.txt", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            m = re.search(r"_(\d{4})$", r["trip_id"])
            if m:
                cle_shape[(r["route_id"], r["direction_id"], m.group(1))].add(r["shape_id"])

    def shape_de(cle):
        s = cle_shape.get(cle, set())
        return next(iter(s)) if len(s) == 1 else None

    def cle_de(t):
        return (t.route_id, str(t.direction_id), t.start_time[:5].replace(":", ""))

    arrets = charger_arrets(S / "hsl/stops.txt")
    ts, c = dernier("hsl_trip_updates")
    m = gtfs_realtime_pb2.FeedMessage()
    m.ParseFromString(c)
    trains = {}
    for e in m.entity:
        t = e.trip_update.trip
        if t.route_id.startswith("300"):
            ordre = sorted(e.trip_update.stop_time_update, key=lambda s: s.stop_sequence)
            trains[cle_de(t)] = [s.stop_id for s in ordre]
    ts2, c2 = dernier("hsl_witness_vehicle_positions")
    m2 = gtfs_realtime_pb2.FeedMessage()
    m2.ParseFromString(c2)
    gps = [(cle_de(e.vehicle.trip), e.vehicle.position.latitude, e.vehicle.position.longitude)
           for e in m2.entity if e.vehicle.trip.route_id.startswith("300")]
    formes = charger_formes(S / "hsl/shapes.txt", {shape_de(k) for k in list(trains) + [g[0] for g in gps]} - {None})
    print(f"\nHelsinki : prévisions du {ts}, GPS du {ts2}, {len(trains)} trains, {len(gps)} véhicules")
    traiter("Helsinki (HSL)", trains, shape_de, arrets, formes, gps)


if __name__ == "__main__":
    boston()
    helsinki()
