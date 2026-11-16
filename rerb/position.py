"""Tâche 3 : estimer la position d'un train à l'instant t (niveau 1, vitesse constante).

Cadrage : docs/cadrage-tache-3.md. Fonction pure : `t` est un paramètre, jamais l'heure du Mac.

Qui fait quoi :
• lire_train       : un EstimatedVehicleJourney PRIM -> Train (heure estimée sinon théorique).
• estimer          : (train, t, distance) -> Estimation (état, distance depuis la gare précédente, périmé).
• Reseau           : distance entre gares NON voisines = somme des couples voisins (chemin unique d'un arbre).
• distance_depuis_reseau : adapte un Reseau (numéros de gares) aux identifiants PRIM.

Décisions de Mathis (13/11/2026) : origine sans arrivée -> arrivée = départ ; terminus sans départ ->
départ = arrivée ; heures incohérentes ou train annulé -> « non placé » avec motif ; données plus
vieilles que 15 min -> drapeau « périmé » (train placé quand même) ; gare précédente retirée ->
« position inconnue ».
"""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from stops import StopNotFound, stop_area_number

SEUIL_PERIME = timedelta(minutes=15)


class DistanceInconnue(ValueError):
    """Pas de distance connue entre deux gares."""


class Etat(Enum):
    A_QUAI = "à quai"
    EN_ROUTE = "en route"
    POSITION_INCONNUE = "position inconnue"
    TERMINE = "terminé"
    NON_PLACE = "non placé"


@dataclass(frozen=True)
class Passage:
    gare: str
    arrivee: "datetime | None"
    depart: "datetime | None"
    annule: bool = False
    reconstruit: bool = False  # heures prises dans la mémoire (gare retirée par PRIM), voir memoire.py
    repare: bool = False       # heure ramenée en arrière pour rester cohérente avec la gare suivante


@dataclass(frozen=True)
class Train:
    passages: list
    recorded_at: "datetime | None"


@dataclass(frozen=True)
class Estimation:
    etat: Etat
    motif: "str | None" = None
    gare: "str | None" = None          # gare à quai, ou gare précédente (en route)
    gare_suivante: "str | None" = None
    fraction: "float | None" = None
    distance_m: "float | None" = None  # depuis `gare`
    longueur_m: "float | None" = None  # en route : longueur du tronçon gare -> gare_suivante
    duree_s: "float | None" = None     # en route : durée annoncée du tronçon (départ -> arrivée suivante)
    perime: bool = False
    reconstruit: bool = False          # une des gares utilisées vient de la mémoire
    repare: bool = False               # une des gares utilisées a une heure réparée


def _heure(texte):
    return datetime.fromisoformat(texte.replace("Z", "+00:00")) if texte else None


def lire_train(ej):
    """EstimatedVehicleJourney (JSON PRIM décodé) -> Train. Estimée sinon théorique ; CANCELLED -> annulé."""
    calls = ej.get("EstimatedCalls") or []
    if isinstance(calls, dict):
        calls = calls.get("EstimatedCall") or []
    passages = []
    for c in calls:
        arrivee = _heure(c.get("ExpectedArrivalTime") or c.get("AimedArrivalTime"))
        depart = _heure(c.get("ExpectedDepartureTime") or c.get("AimedDepartureTime"))
        annule = "CANCELLED" in (c.get("ArrivalStatus"), c.get("DepartureStatus"))
        passages.append(Passage(c["StopPointRef"]["value"], arrivee, depart, annule))
    return Train(passages, _heure(ej.get("RecordedAtTime")))


def _non_place(motif, perime):
    return Estimation(Etat.NON_PLACE, motif=motif, perime=perime)


def estimer(train, t, distance, seuil_perime=SEUIL_PERIME):
    """Position estimée du train à l'instant t.

    distance(gare_a, gare_b) -> mètres le long des rails ; lève DistanceInconnue si inconnue.
    """
    perime = train.recorded_at is None or (t - train.recorded_at) > seuil_perime

    if not train.passages:
        return _non_place("aucun passage", perime)
    if any(p.annule for p in train.passages):
        return _non_place("train annulé", perime)

    # trier par heure (53 trains sur 56 arrivent en désordre), sans heure -> manquantes
    def cle(p):
        return p.arrivee or p.depart

    if any(cle(p) is None for p in train.passages):
        return _non_place("heures manquantes", perime)
    ordre = sorted(train.passages, key=cle)

    # origine sans arrivée -> arrivée = départ ; terminus sans départ -> départ = arrivée
    horaires = []
    dernier = len(ordre) - 1
    for i, p in enumerate(ordre):
        arrivee, depart = p.arrivee, p.depart
        if arrivee is None and i == 0:
            arrivee = depart
        if depart is None and i == dernier:
            depart = arrivee
        if arrivee is None or depart is None:
            return _non_place("heures manquantes", perime)
        horaires.append((p.gare, arrivee, depart, p.reconstruit, p.repare))

    # cohérence : on repart après être arrivé, et on arrive après être reparti de la gare d'avant
    for i, (_, arrivee, depart, _r, _p) in enumerate(horaires):
        if depart < arrivee:
            return _non_place("heures incohérentes", perime)
        if i > 0 and arrivee < horaires[i - 1][2]:
            return _non_place("heures incohérentes", perime)

    if t < horaires[0][1]:
        return Estimation(Etat.POSITION_INCONNUE, motif="avant la 1re gare de la liste", perime=perime)
    if t > horaires[-1][2]:
        return Estimation(Etat.TERMINE, perime=perime)

    for i, (gare, arrivee, depart, recon, repare) in enumerate(horaires):
        if arrivee <= t <= depart:
            return Estimation(Etat.A_QUAI, gare=gare, distance_m=0.0, perime=perime,
                              reconstruit=recon, repare=repare)
        if i + 1 < len(horaires) and depart < t < horaires[i + 1][1]:
            suivante, arrivee_suiv = horaires[i + 1][0], horaires[i + 1][1]
            recon = recon or horaires[i + 1][3]
            repare = repare or horaires[i + 1][4]
            try:
                longueur = distance(gare, suivante)
            except DistanceInconnue as erreur:
                return _non_place(f"distance inconnue ({erreur})", perime)
            f = (t - depart) / (arrivee_suiv - depart)
            return Estimation(Etat.EN_ROUTE, gare=gare, gare_suivante=suivante,
                              fraction=f, distance_m=f * longueur, perime=perime,
                              longueur_m=longueur, duree_s=(arrivee_suiv - depart).total_seconds(),
                              reconstruit=recon, repare=repare)
    raise AssertionError("inatteignable : t est entre la 1re arrivée et le dernier départ")


class Reseau:
    """Arbre des gares voisines : distance entre deux gares = somme le long du chemin unique."""

    def __init__(self, paires):
        """paires : {(gare_a, gare_b): mètres} pour les couples de gares voisines."""
        self._voisins = {}
        for (a, b), m in paires.items():
            self._voisins.setdefault(a, []).append((b, m))
            self._voisins.setdefault(b, []).append((a, m))

    def distance(self, a, b):
        for g in (a, b):
            if g not in self._voisins:
                raise DistanceInconnue(f"gare inconnue : {g}")
        if a == b:
            return 0.0
        vus = {a: 0.0}
        file = deque([a])
        while file:
            courant = file.popleft()
            for voisin, m in self._voisins[courant]:
                if voisin not in vus:
                    vus[voisin] = vus[courant] + m
                    if voisin == b:
                        return vus[voisin]
                    file.append(voisin)
        raise DistanceInconnue(f"gares non reliées : {a} et {b}")

    def chemin(self, a, b):
        """Gares traversées de a à b (chemin unique de l'arbre), extrémités comprises."""
        for g in (a, b):
            if g not in self._voisins:
                raise DistanceInconnue(f"gare inconnue : {g}")
        parents = {a: None}
        file = deque([a])
        while file:
            courant = file.popleft()
            if courant == b:
                break
            for voisin, _ in self._voisins[courant]:
                if voisin not in parents:
                    parents[voisin] = courant
                    file.append(voisin)
        if b not in parents:
            raise DistanceInconnue(f"gares non reliées : {a} et {b}")
        gares = []
        while b is not None:
            gares.append(b)
            b = parents[b]
        return gares[::-1]


def distance_depuis_reseau(reseau):
    """Adapte un Reseau indexé par numéro de gare aux identifiants PRIM 'STIF:StopArea:SP:<n>:'."""
    def distance(ref_a, ref_b):
        try:
            return reseau.distance(stop_area_number(ref_a), stop_area_number(ref_b))
        except StopNotFound as erreur:
            raise DistanceInconnue(str(erreur)) from None
    return distance
