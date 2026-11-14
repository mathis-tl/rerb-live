"""Tâche 3 : prévisions et positions HSL (Helsinki) -> format de position.py et de mbta.py.

Cadrage : docs/cadrage-tache-3.md (E5, S4).
• cle_course      : (ligne, sens GTFS 0/1, jour d'exploitation, heure de départ HHMM). Le trip_id du temps réel est
                    vide, cette clé relie prévision (GTFS-RT) et GPS (MQTT HFP).
• lire_train_hsl  : mise à jour de course GTFS-RT décodée (dict) -> Train. Heures en secondes epoch (UTC).
• lire_point_hfp  : message MQTT HFP décodé -> PointGps (instant = `tst`, instant réel de la mesure GPS).

Choix : un arrêt SKIPPED est retiré (le train n'y passe pas) ; une course CANCELED -> tous les passages annulés.
HSL garde les arrêts passés dans les prévisions : pas de mémoire M2 nécessaire.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from geometry import _dist

from mbta import PointGps
from position import Passage, Train

HELSINKI = ZoneInfo("Europe/Helsinki")
SEUIL_RAME = 300.0  # m : S4 option C, au-delà, plusieurs véhicules sous une même clé = trains différents
MOTIF_RAME = "autre unité d'une rame couplée"
MOTIF_AMBIGU = "course ambiguë (plusieurs trains à plus de 300 m)"


def cle_course(route_id, direction_id, start_date, start_time):
    """start_time : « HH:MM » ou « HH:MM:SS » (heure locale). start_date : « AAAAMMJJ »."""
    return (route_id, int(direction_id), start_date, start_time[:2] + start_time[3:5])


def _heure(secondes):
    return datetime.fromtimestamp(secondes, timezone.utc) if secondes else None


def lire_train_hsl(maj, recorded_at):
    annulee = maj.get("schedule_relationship") == "CANCELED"
    passages = []
    for u in maj["stop_time_updates"]:
        if u["schedule_relationship"] == "SKIPPED":
            continue
        passages.append(Passage(u["stop_id"], _heure(u["arrival"]), _heure(u["departure"]), annulee))
    return Train(passages, recorded_at)


def lire_point_hfp(message, recu):
    vp = message["VP"]
    course = None
    if vp.get("route") and vp.get("dir") in ("1", "2", 1, 2) and vp.get("oday") and vp.get("start"):
        depart = vp["start"]
        local = datetime.fromisoformat(vp["tst"].replace("Z", "+00:00")).astimezone(HELSINKI)
        debut = local.date()
        if int(depart[:2]) * 60 + int(depart[3:5]) > local.hour * 60 + local.minute + 12 * 60:
            debut -= timedelta(days=1)  # parti la veille calendaire, encore en route après minuit
        if debut.strftime("%Y%m%d") > vp["oday"].replace("-", ""):  # GTFS-RT : « 25:51 », HFP : « 01:51 »
            depart = f"{int(depart[:2]) + 24}{depart[2:]}"
        course = cle_course(vp["route"], int(vp["dir"]) - 1, vp["oday"].replace("-", ""), depart)
    instant = datetime.fromisoformat(vp["tst"].replace("Z", "+00:00"))
    statut = "STOPPED_AT" if vp.get("stop") else "IN_TRANSIT_TO"
    return PointGps(course, vp.get("lat"), vp.get("long"), instant, statut, recu)


def choisir_rame(candidats):
    """candidats : [(numéro de véhicule, PointGps)] de la même clé de course au même instant (S4, option C).
    -> (point retenu ou None, motif des écartés ou None, nombre d'écartés).
    Tous à moins de SEUIL_RAME les uns des autres (toutes les paires) : rame couplée, on garde le plus petit
    numéro. Sinon : collision de clés, tous écartés."""
    if len(candidats) == 1:
        return candidats[0][1], None, 0
    pts = [(p.latitude, p.longitude) for _, p in candidats]
    if any(_dist(a, b) > SEUIL_RAME for a in pts for b in pts):
        return None, MOTIF_AMBIGU, len(candidats)
    return min(candidats, key=lambda c: c[0])[1], MOTIF_RAME, len(candidats) - 1
