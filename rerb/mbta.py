"""Tâche 3 : prévisions MBTA (Boston) -> Train au format de position.py.

Cadrage : docs/cadrage-tache-3.md (E5 : Boston d'abord, jointure par identifiant de course).

• grouper_par_course : corps JSON:API d'un relevé -> {trip_id: [prédictions]}.
• lire_train_mbta    : prédictions d'une course -> Train (gares triées par stop_sequence, doublons de ligne
                       supprimés, heures en UTC).

Seul `CANCELLED` marque un passage annulé (`SKIPPED` = arrêt sauté, pas train supprimé).
`recorded_at` = heure de réception du relevé par le collecteur (`ts_recv_utc`).
"""
from collections import defaultdict
from datetime import datetime, timezone

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
