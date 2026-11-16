"""Comparaison estimated-timetable (ET) / stop-monitoring par gare (SM) : fonctions pures (aucun réseau, bibliothèque standard).
Un « passage » = dict {course, gare (référence PRIM), arrivee, depart, recorded_at} (datetimes UTC). Voir
exploration/analyser_comparaison.py pour l'usage sur les relevés bruts."""
from datetime import datetime

from gare import LIGNE


def _h(texte):
    return datetime.fromisoformat(texte.replace("Z", "+00:00")) if texte else None


def _v(x):
    return x.get("value") if isinstance(x, dict) else x


def passages_sm(corps, ligne=LIGNE):
    """Réponse `stop-monitoring` -> passages de la ligne (RER B). Réponse vide ou illisible : liste vide."""
    try:
        visites = corps["StopMonitoringDelivery"][0].get("MonitoredStopVisit") or []
    except (KeyError, IndexError, TypeError, AttributeError):
        return []
    sortie = []
    for v in visites:
        j = v.get("MonitoredVehicleJourney") or {}
        if _v(j.get("LineRef")) != ligne:
            continue
        a = j.get("MonitoredCall") or {}
        arr, dep = _h(a.get("ExpectedArrivalTime")), _h(a.get("ExpectedDepartureTime"))
        if arr is None and dep is None:
            continue
        sortie.append({"course": _v((j.get("FramedVehicleJourneyRef") or {}).get("DatedVehicleJourneyRef")),
                       "gare": _v(v.get("MonitoringRef")), "arrivee": arr, "depart": dep,
                       "recorded_at": _h(v.get("RecordedAtTime")), "a_quai": bool(a.get("VehicleAtStop"))})
    return sortie


def passages_et(livraison, memoire):
    """Livraison `estimated-timetable` -> passages (trains consolidés comme le serveur : `consolider_livraison`)."""
    from serveur import consolider_livraison
    try:
        trains = consolider_livraison(livraison, memoire)
    except (KeyError, IndexError, TypeError, AttributeError):
        return []
    return [{"course": ref, "gare": ps.gare, "arrivee": ps.arrivee, "depart": ps.depart, "recorded_at": train.recorded_at}
            for ref, (train, _) in trains.items() for ps in train.passages if not ps.annule]


def segments_vitesse(passages, distance, vmax=33.0):
    """Vitesse implicite entre passages consécutifs (par ordre d'heure) d'une même course : distance(gare_a, gare_b) / durée
    (départ de a -> arrivée en b). -> (nombre de segments, segments > vmax, liste des vitesses m/s)."""
    par_course = {}
    for ps in passages:
        par_course.setdefault(ps["course"], []).append(ps)
    n, mauvais, vitesses = 0, [], []
    for course, liste in par_course.items():
        liste = sorted(liste, key=lambda x: x["arrivee"] or x["depart"])
        for a, b in zip(liste, liste[1:]):
            ta, tb = a["depart"] or a["arrivee"], b["arrivee"] or b["depart"]
            if ta is None or tb is None or tb <= ta:
                continue
            try:
                d = distance(a["gare"], b["gare"])
            except (KeyError, ValueError):
                continue
            v = d / (tb - ta).total_seconds()
            n += 1
            vitesses.append(v)
            if v > vmax:
                mauvais.append({"course": course, "de": a["gare"], "vers": b["gare"], "m": d, "s": (tb - ta).total_seconds(), "v": v})
    return n, mauvais, vitesses


def _heure_cle(ps):
    return ps["depart"] or ps["arrivee"]


def ecarts_et_sm(et, sm):
    """Pour les (course, gare) présents dans les deux : heure SM - heure ET, en secondes (départ, sinon arrivée)."""
    index = {(x["course"], x["gare"]): _heure_cle(x) for x in et if _heure_cle(x)}
    return [(_heure_cle(x) - index[(x["course"], x["gare"])]).total_seconds()
            for x in sm if _heure_cle(x) and (x["course"], x["gare"]) in index]


def age_s(instant_recu, enregistre):
    """Âge d'une donnée à la réception : réception - heure d'enregistrement par la source (None si inconnue)."""
    return None if enregistre is None else (instant_recu - enregistre).total_seconds()


def changements(tours, seuil_s=2.0):
    """`tours` : liste (un par relevé) de listes de passages. Compte les (course, gare) présents dans deux relevés consécutifs
    (paires suivies) et ceux dont l'heure estimée a bougé de plus de `seuil_s` secondes. -> (suivis, changés)."""
    suivis = changes = 0
    for avant, apres in zip(tours, tours[1:]):
        index = {(x["course"], x["gare"]): _heure_cle(x) for x in avant if _heure_cle(x)}
        for x in apres:
            k = (x["course"], x["gare"])
            if k in index and _heure_cle(x):
                suivis += 1
                if abs((_heure_cle(x) - index[k]).total_seconds()) > seuil_s:
                    changes += 1
    return suivis, changes


def quantile(valeurs, q):
    if not valeurs:
        return None
    s = sorted(valeurs)
    return s[min(len(s) - 1, int(q * len(s)))]
