"""Tâche 3 : prévisions MBTA (Boston) -> Train au format de position.py.

Cadrage : docs/cadrage-tache-3.md (E5 : Boston d'abord, jointure par identifiant de course).

• grouper_par_course : corps JSON:API d'un relevé -> {trip_id: [prédictions]}.
• lire_gps_mbta      : relevé mbta_vehicles -> points GPS (instant = updated_at, J1).
• apparier          : point GPS + relevés de prévisions -> (Train, None) ou (None, motif d'écart) (J2, J3, E2a).
• lire_train_mbta    : prédictions d'une course -> Train (gares triées par stop_sequence, doublons de ligne
                       supprimés, heures en UTC).

Seul `CANCELLED` marque un passage annulé (`SKIPPED` = arrêt sauté, pas train supprimé).
`recorded_at` = heure de réception du relevé par le collecteur (`ts_recv_utc`).
"""
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from position import Passage, Train


def _heure(texte):
    return datetime.fromisoformat(texte).astimezone(timezone.utc) if texte else None


def grouper_par_course(corps):
    groupes = defaultdict(list)
    for p in corps.get("data", []):
        trip = p["relationships"]["trip"]["data"]
        if trip:
            groupes[trip["id"]].append(p)
    return dict(groupes)


SEUIL_GPS_PERIME = timedelta(seconds=120)  # J2 : âge = reçu - updated_at ; 0,5 % des positions au-delà de 300 s


@dataclass(frozen=True)
class PointGps:
    course: "str | None"
    latitude: "float | None"
    longitude: "float | None"
    instant: datetime   # updated_at : instant réel de la position (J1), pas l'heure de réception
    statut: str         # IN_TRANSIT_TO ou STOPPED_AT
    recu: datetime      # ts_recv_utc du relevé


def lire_gps_mbta(corps, recu):
    points = []
    for v in corps.get("data", []):
        a = v["attributes"]
        trip = v["relationships"]["trip"]["data"]
        points.append(PointGps(trip["id"] if trip else None, a["latitude"], a["longitude"],
                               _heure(a["updated_at"]), a["current_status"], recu))
    return points


def apparier(point, releves, seuil=SEUIL_GPS_PERIME):
    """releves : [(recu, grouper_par_course(corps))] trié par `recu` croissant.

    E2a : dernier relevé de prévisions reçu au plus tard à l'instant du GPS, jamais le futur.
    """
    if point.recu - point.instant > seuil:
        return None, "GPS périmé"
    i = bisect_right([r[0] for r in releves], point.instant)
    if i == 0:
        return None, "aucun relevé de prévisions avant le GPS"
    recu, groupes = releves[i - 1]
    if point.course not in groupes:
        return None, "course absente des prévisions"
    return lire_train_mbta(groupes[point.course], recu), None


def lire_train_mbta(predictions, recorded_at):
    # une course partagée entre deux lignes (CR-Fairmount / CR-Franklin) liste chaque arrêt deux fois,
    # copies identiques (0 divergence sur 7 111 couples le 13/11/2026) : une seule gardée par stop_sequence
    uniques = {p["attributes"]["stop_sequence"]: p for p in predictions}
    ordre = [uniques[s] for s in sorted(uniques)]
    passages = []
    for p in ordre:
        a = p["attributes"]
        passages.append(Passage(
            p["relationships"]["stop"]["data"]["id"],
            _heure(a["arrival_time"]),
            _heure(a["departure_time"]),
            a["schedule_relationship"] == "CANCELLED",
        ))
    return Train(passages, recorded_at)
