"""Tâche 4, J4 : source de DÉMONSTRATION. Trains INVENTÉS qui roulent sur la vraie suite de gares, au format PRIM.

Sert à voir la carte sans appeler PRIM ni utiliser de clé (`serveur.py --demo`). Ces trains n'existent pas : le
serveur l'affiche dans la page. Fonction pure et déterministe : mêmes (instant, chaîne) -> même réponse. Les trains
partent à des heures fixes (toutes les `gap_s` secondes depuis minuit UTC), donc ils avancent d'un appel à l'autre.
• sens alterné aller / retour sur la chaîne de gares ; vitesse 14,7 m/s (ordre de grandeur J2), arrêt 60 s ;
• retards variés (0, 2 et 4 min) ; un train annulé (« non placé ») et un train périmé (relevé vieux de 30 min).
"""
from datetime import datetime, timedelta, timezone

VITESSE = 14.7
ARRET_S = 60
RETARDS_S = (0, 120, 240)


def _iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _appel(gare, arrivee, depart, retard_s):
    return {"StopPointRef": {"value": gare},
            "ExpectedArrivalTime": _iso(arrivee), "ExpectedDepartureTime": _iso(depart),
            "AimedArrivalTime": _iso(arrivee - timedelta(seconds=retard_s)),
            "AimedDepartureTime": _iso(depart - timedelta(seconds=retard_s))}


def _course(ref, enregistre, appels):
    return {"DatedVehicleJourneyRef": ref, "RecordedAtTime": _iso(enregistre), "EstimatedCalls": appels}


def _trajet(chaine, distance, depart, retard_s):
    """Appels d'un trajet sur `chaine` qui part à `depart` : arrêt de ARRET_S par gare, VITESSE entre deux gares."""
    appels, t = [], depart
    for i, gare in enumerate(chaine):
        arrivee = t
        quitte = arrivee + timedelta(seconds=ARRET_S) if i + 1 < len(chaine) else arrivee
        appels.append(_appel(gare, arrivee, quitte, retard_s))
        if i + 1 < len(chaine):
            t = quitte + timedelta(seconds=distance(gare, chaine[i + 1]) / VITESSE)
    return appels


def livraison_demo(maintenant, chaine, distance, nombre=20, gap_s=150):
    """Réponse au format PRIM `ServiceDelivery` avec `nombre` courses : nombre - 2 trajets réguliers, 1 annulé, 1 périmé."""
    minuit = maintenant.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    j_courant = int((maintenant - minuit).total_seconds() // gap_s)
    courses = []
    for m in range(nombre - 2):
        j = j_courant - m
        sens = chaine if j % 2 == 0 else chaine[::-1]
        depart = minuit + timedelta(seconds=j * gap_s)
        retard = RETARDS_S[j % len(RETARDS_S)]
        courses.append(_course(f"DEMO-{'A' if j % 2 == 0 else 'R'}-{j}", maintenant - timedelta(seconds=20),
                               _trajet(sens, distance, depart, retard)))
    # un train périmé : trajet normal mais relevé vieux de 30 min (PRIM ne l'a plus mis à jour)
    j = j_courant - 1
    courses.append(_course(f"DEMO-PERIME-{j}", maintenant - timedelta(minutes=30),
                           _trajet(chaine, distance, minuit + timedelta(seconds=j * gap_s) + timedelta(seconds=gap_s // 2), 0)))
    # un train annulé : une gare annulée, donc « non placé » (jamais masqué)
    annule = _trajet(chaine[:2], distance, maintenant + timedelta(minutes=5), 0)
    annule[1]["ArrivalStatus"] = "CANCELLED"
    courses.append(_course("DEMO-ANNULE", maintenant - timedelta(seconds=20), annule))
    return {"EstimatedTimetableDelivery": [{"EstimatedJourneyVersionFrame": [{"EstimatedVehicleJourney": courses}]}]}
