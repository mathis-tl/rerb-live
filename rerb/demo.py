"""Tâche 4, J4 : source de DÉMONSTRATION. Trains INVENTÉS qui roulent sur la vraie suite de gares, au format PRIM.

Sert à voir la carte sans appeler PRIM ni utiliser de clé (`serveur.py --demo`). Ces trains n'existent pas : le
serveur l'affiche dans la page. Fonction pure et déterministe : mêmes (instant, chaîne) -> même réponse. Les trains
partent à des heures fixes (toutes les `gap_s` secondes depuis minuit UTC), donc ils avancent d'un appel à l'autre.
• sens alterné aller / retour sur la chaîne de gares ; vitesse 14,7 m/s (ordre de grandeur J2), arrêt 60 s ;
• retards variés (0, 2 et 4 min) ; un train annulé (« non placé ») et un train périmé (relevé vieux de 30 min).
"""
import zlib
from datetime import datetime, timedelta, timezone

VITESSE = 14.7
ARRET_S = 60
RETARDS_S = (0, 120, 240)
PAS_REVISION_S = 30        # `revisions=True` : PRIM « révise » ses heures toutes les 30 s (cadence de stop-monitoring)
AMPLITUDE_REVISION_S = 45  # révision de ±45 s au plus, moins forte à l'approche de la gare
GROS_RETARD_S = 150        # un train sur six prend 150 s de retard d'un coup, 5 min après son départ


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


def _reviser(ref, appels, maintenant, depart_train, gros_retard):
    """Appels dont les arrivées à venir sont « révisées » comme le fait PRIM (bruit de ±AMPLITUDE_REVISION_S qui change à chaque fenêtre de
    PAS_REVISION_S, plus un retard soudain de GROS_RETARD_S pour `gros_retard`). Arrivée et départ sont décalés ensemble (séjour conservé) ;
    les gares déjà atteintes ne bougent pas ; l'arrivée ne précède jamais de moins de 5 s le départ de la gare d'avant."""
    fenetre = int(maintenant.timestamp() // PAS_REVISION_S)
    debut_fenetre = datetime.fromtimestamp(fenetre * PAS_REVISION_S, timezone.utc)
    sortie, depart_prec = [], None
    for c in appels:
        arrivee, depart = _heure(c["ExpectedArrivalTime"]), _heure(c["ExpectedDepartureTime"])
        if arrivee > debut_fenetre:
            restant = (arrivee - debut_fenetre).total_seconds()   # début de fenêtre : les heures restent stables pendant PAS_REVISION_S
            ampleur = AMPLITUDE_REVISION_S * (0.3 + 0.7 * min(1.0, restant / 180.0))
            h = zlib.crc32(f"{ref}|{c['StopPointRef']['value']}|{fenetre}".encode()) % 1001 / 500.0 - 1.0   # [-1 ; 1]
            decalage = timedelta(seconds=h * ampleur)
            if gros_retard and debut_fenetre > depart_train + timedelta(minutes=5):
                decalage += timedelta(seconds=GROS_RETARD_S)
            if depart_prec is not None:
                decalage = max(decalage, depart_prec + timedelta(seconds=5) - arrivee)
            c = {**c, "ExpectedArrivalTime": _iso(arrivee + decalage), "ExpectedDepartureTime": _iso(depart + decalage)}
            arrivee, depart = arrivee + decalage, depart + decalage
        depart_prec = depart
        sortie.append(c)
    return sortie


def livraison_demo(maintenant, chaine, distance, nombre=20, gap_s=150, revisions=False):
    """Réponse au format PRIM `ServiceDelivery` avec `nombre` courses : nombre - 2 trajets réguliers, 1 annulé, 1 périmé.
    `revisions=True` : les heures d'arrivée à venir sont révisées à chaque fenêtre de 30 s (voir `_reviser`), pour juger le lissage à l'œil."""
    minuit = maintenant.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    j_courant = int((maintenant - minuit).total_seconds() // gap_s)
    courses = []
    for m in range(nombre - 2):
        j = j_courant - m
        sens = chaine if j % 2 == 0 else chaine[::-1]
        depart = minuit + timedelta(seconds=j * gap_s)
        retard = RETARDS_S[j % len(RETARDS_S)]
        ref = f"DEMO-{'A' if j % 2 == 0 else 'R'}-{j}"
        appels = _trajet(sens, distance, depart, retard)
        if revisions:
            appels = _reviser(ref, appels, maintenant, depart, gros_retard=j % 6 == 0)
        courses.append(_course(ref, maintenant - timedelta(seconds=20), appels))
    # un train périmé : trajet normal mais relevé vieux de 30 min (PRIM ne l'a plus mis à jour)
    j = j_courant - 1
    courses.append(_course(f"DEMO-PERIME-{j}", maintenant - timedelta(minutes=30),
                           _trajet(chaine, distance, minuit + timedelta(seconds=j * gap_s) + timedelta(seconds=gap_s // 2), 0)))
    # un train annulé : une gare annulée, donc « non placé » (jamais masqué)
    annule = _trajet(chaine[:2], distance, maintenant + timedelta(minutes=5), 0)
    annule[1]["ArrivalStatus"] = "CANCELLED"
    courses.append(_course("DEMO-ANNULE", maintenant - timedelta(seconds=20), annule))
    return {"EstimatedTimetableDelivery": [{"EstimatedJourneyVersionFrame": [{"EstimatedVehicleJourney": courses}]}]}


def _heure(texte):
    return datetime.fromisoformat(texte.replace("Z", "+00:00"))


def stop_monitoring_demo(maintenant, gare, chaine, distance, nom_gare, nombre=20, gap_s=150, revisions=False):
    """Réponse au format `stop-monitoring` de PRIM pour `gare`, tirée des mêmes trains inventés que `livraison_demo`.
    `nom_gare(ref)` donne le nom de la gare d'arrivée (DestinationName). Gare hors chaîne : réponse sans visite."""
    visites = []
    liv = livraison_demo(maintenant, chaine, distance, nombre, gap_s, revisions)
    for ej in liv["EstimatedTimetableDelivery"][0]["EstimatedJourneyVersionFrame"][0]["EstimatedVehicleJourney"]:
        appels = ej["EstimatedCalls"]
        ici = next((c for c in appels if c["StopPointRef"]["value"] == gare), None)
        if ici is None:
            continue
        terminus = max(appels, key=lambda c: _heure(c["ExpectedArrivalTime"]))["StopPointRef"]["value"]
        arrivee, depart = _heure(ici["ExpectedArrivalTime"]), _heure(ici["ExpectedDepartureTime"])
        appel = {"VehicleAtStop": arrivee <= maintenant <= depart}
        appel.update({k: ici[k] for k in ("ExpectedArrivalTime", "ExpectedDepartureTime", "AimedArrivalTime", "AimedDepartureTime")})
        if "CANCELLED" in (ici.get("ArrivalStatus"), ici.get("DepartureStatus")):
            appel["DepartureStatus"] = "cancelled"
        visites.append({"MonitoredVehicleJourney": {
            "LineRef": {"value": "STIF:Line::C01743:"},
            "FramedVehicleJourneyRef": {"DatedVehicleJourneyRef": ej["DatedVehicleJourneyRef"]},
            "DestinationName": [{"value": nom_gare(terminus)}], "VehicleJourneyName": [{"value": "DEMO"}],
            "MonitoredCall": appel}})
    return {"StopMonitoringDelivery": [{"MonitoredStopVisit": visites}]}
