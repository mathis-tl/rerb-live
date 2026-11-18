"""Audit de la carte : anomalies d'une série de relevés de `/trains` (fonctions pures, aucun réseau).

Entrée : `serie` = [(t_secondes, [train, ...]), ...] dans l'ordre du temps ; chaque train = dict de `/trains`
(course, etat, lat, lon, gare, gare_suivante, perime). Sortie : dict de listes d'anomalies, chacune expliquée.
• sauts            : déplacement plus grand que VITESSE_MAX_MS x intervalle (+ marge) entre deux relevés ;
• disparitions     : train qui n'est plus dessiné, avec la cause (terminus, sortie de la liste PRIM, terminé) ;
• immobiles        : train en route resté au même endroit trop longtemps (à quai : seuil 2,5 fois plus long) ;
• bascules_perime  : passage de périmé à non périmé ou l'inverse.
Lu par `exploration/auditer_carte.py`, qui interroge le serveur LOCAL seulement.
"""
import math
from urllib.parse import urlsplit

VITESSE_MAX_MS = 33.0      # même plafond que serveur.py
MARGE_M = 15.0             # tolérance : arrondis, virages
SEUIL_IMMOBILE_M = 3.0


def metres(a, b):
    return math.hypot((b["lon"] - a["lon"]) * 74000, (b["lat"] - a["lat"]) * 111000)


def _place(t):
    return t.get("lat") is not None and t.get("lon") is not None


def url_autorisee(url):
    """Seulement le serveur local (jamais PRIM ni un autre hôte) : l'audit ne consomme aucun appel."""
    try:
        morceaux = urlsplit(url)
        return morceaux.scheme == "http" and morceaux.hostname in ("127.0.0.1", "localhost")
    except ValueError:
        return False


def auditer(serie, immobile_max_s=180):
    sauts, disparitions, immobiles, bascules = [], [], [], []
    precedent, t_prec = {}, None
    ancre = {}   # course -> (t de début, train de référence) tant que le train ne bouge pas

    def clore(course, t_fin):
        t0, ref = ancre.pop(course)
        duree = t_fin - t0
        seuil = immobile_max_s * (2.5 if ref["etat"] == "à quai" else 1)
        if duree >= seuil:
            immobiles.append({"course": course, "t": t0, "duree_s": duree, "etat": ref["etat"], "gare": ref.get("gare")})

    for t, trains in serie:
        courants = {x["course"]: x for x in trains}
        for course, x in courants.items():
            p = precedent.get(course)
            if p is not None:
                if _place(p) and _place(x):
                    m = metres(p, x)
                    if m > VITESSE_MAX_MS * (t - t_prec) + MARGE_M:
                        sauts.append({"course": course, "t": t, "metres": m, "avant": p["etat"], "apres": x["etat"],
                                      "gare": x.get("gare"), "gare_suivante": x.get("gare_suivante")})
                if _place(p) and not _place(x):
                    disparitions.append({"course": course, "t": t, "cause": f"{x['etat']} (plus dessiné)", "gare": p.get("gare")})
                if bool(p.get("perime")) != bool(x.get("perime")):
                    bascules.append({"course": course, "t": t, "perime": bool(x.get("perime")), "gare": x.get("gare")})
            if _place(x):
                if course in ancre and metres(ancre[course][1], x) >= SEUIL_IMMOBILE_M:
                    clore(course, t)
                if course not in ancre:
                    ancre[course] = (t, x)
            elif course in ancre:
                clore(course, t)
        for course, p in precedent.items():
            if course in courants:
                continue
            if _place(p):
                # /trains ne donne pas `gare_suivante` à quai : un train disparu en gare est un terminus probable, sinon il est sorti de la liste
                disparitions.append({"course": course, "t": t, "cause": "terminus probable" if p["etat"] == "à quai" else "sortie de la liste PRIM",
                                     "gare": p.get("gare")})
            if course in ancre:
                clore(course, t)
        precedent, t_prec = courants, t
    for course in list(ancre):
        clore(course, serie[-1][0])
    return {"sauts": sauts, "disparitions": disparitions, "immobiles": immobiles, "bascules_perime": bascules}
