"""Vérification du diagnostic du council (17/11) : roulages annoncés contre borne physique.

Module pur (bibliothèque standard + stops), aucune lecture de fichier, aucun réseau.
Rôle : à partir de relevés estimated-timetable déjà décodés, mesurer pour chaque tronçon orienté
(gare A -> gare B consécutives dans une course) :
• roulage annoncé   = arrivée estimée à B - départ estimé de A ;
• roulage théorique = arrivée théorique (Aimed) à B - départ théorique de A ;
• part des roulages annoncés STRICTEMENT sous la borne physique (minimum OSM + MI 84).

Deux comptages :
• par occurrence : chaque (relevé, course, tronçon) compte une fois (comme le script jetable du council) ;
• par course : une valeur par (course, tronçon), celle du dernier relevé reçu avant l'heure
  estimée de départ de A dans ce relevé.
"""
from dataclasses import dataclass, field
from zlib import crc32
from datetime import datetime
from statistics import median

from stops import StopNotFound, stop_area_number


@dataclass(frozen=True)
class Appel:
    """Un passage d'une course à une gare, avec les quatre heures publiées (None si absente)."""
    gare: str
    arr_est: "datetime | None" = None
    dep_est: "datetime | None" = None
    arr_theo: "datetime | None" = None
    dep_theo: "datetime | None" = None
    annule: bool = False


@dataclass(frozen=True)
class Observation:
    recu: datetime                       # instant de réception du relevé
    course: str
    depart_gare: str
    arrivee_gare: str
    depart_est: "datetime | None"        # départ estimé de A dans ce relevé
    annonce: "float | None"              # secondes, None si une heure estimée manque
    theorique: "float | None"            # secondes, None si une heure théorique manque
    a_origine: bool = False              # A est la première gare de la course (dans l'ordre de tri)
    retard_dep: "float | None" = None    # départ estimé - départ théorique à A (s)
    retard_arr_a: "float | None" = None  # arrivée estimée - arrivée théorique à A (s)
    retard_arr_b: "float | None" = None  # arrivée estimée - arrivée théorique à B (s)
    arret_est: "float | None" = None     # départ estimé - arrivée estimée à A (s)
    arret_theo: "float | None" = None    # départ théorique - arrivée théorique à A (s)

    @property
    def cle(self):
        return (self.depart_gare, self.arrivee_gare)


@dataclass
class Bilan:
    sous: int = 0
    total: int = 0
    sans_borne: int = 0                  # observations dont le tronçon n'a pas de borne (écartées)
    par_troncon: dict = field(default_factory=dict)   # (A, B) -> (sous, total)

    @property
    def part(self):
        return self.sous / self.total if self.total else float("nan")


def _heure(texte):
    return datetime.fromisoformat(texte.replace("Z", "+00:00")) if texte else None


def lire_course(ej):
    """EstimatedVehicleJourney (JSON décodé) -> (référence de course, liste d'Appel).

    Pas de repli estimée -> théorique. Les gares illisibles (non StopArea) sont ignorées.
    """
    ref = (ej.get("DatedVehicleJourneyRef") or {}).get("value")
    calls = ej.get("EstimatedCalls") or []
    if isinstance(calls, dict):
        calls = calls.get("EstimatedCall") or []
    appels = []
    for c in calls:
        try:
            gare = stop_area_number(c["StopPointRef"]["value"])
        except (StopNotFound, KeyError, TypeError):
            continue
        annule = "CANCELLED" in (c.get("ArrivalStatus"), c.get("DepartureStatus"))
        appels.append(Appel(gare, _heure(c.get("ExpectedArrivalTime")), _heure(c.get("ExpectedDepartureTime")),
                            _heure(c.get("AimedArrivalTime")), _heure(c.get("AimedDepartureTime")), annule))
    return ref, appels


def lire_origine(ej):
    """Numéro de la gare d'origine de la course (OriginRef), ou None si absent ou illisible."""
    try:
        return stop_area_number((ej.get("OriginRef") or {}).get("value"))
    except StopNotFound:
        return None


def _cle_theorique(a):
    return a.arr_theo or a.dep_theo


def _cle_estimee(a):
    return a.arr_est or a.dep_est


def troncons(appels, tri="theorique"):
    """Paires (A, B) d'appels consécutifs après tri par heure ('theorique' = Aimed, 'estime' = Expected).

    Un appel sans aucune heure de tri rend la position des autres incertaine : la course
    entière donne alors zéro tronçon (choix prudent).
    """
    if tri == "theorique":
        cle = _cle_theorique
    elif tri == "estime":
        cle = _cle_estimee
    else:
        raise ValueError(f"tri inconnu : {tri!r}")
    if any(cle(a) is None for a in appels):
        return []
    ordre = sorted(appels, key=cle)
    return list(zip(ordre, ordre[1:]))


def _secondes(fin, debut):
    return None if fin is None or debut is None else (fin - debut).total_seconds()


def roulage_annonce(a, b):
    return _secondes(b.arr_est, a.dep_est)


def roulage_theorique(a, b):
    return _secondes(b.arr_theo, a.dep_theo)


def borne_du_troncon(bornes, a, b):
    """Borne (s) du tronçon a -> b ; les bornes sont données sans orientation ('a|b' ou 'b|a')."""
    v = bornes.get(f"{a}|{b}")
    return v if v is not None else bornes.get(f"{b}|{a}")


def observer(recu, courses, tri="theorique", ecarter_annules=False):
    """Observations d'un relevé. courses = [(référence, [Appel])] ou [(référence, [Appel], origine)] ; recu = instant de réception.

    Origine donnée (numéro de gare, cf. lire_origine) : a_origine = A est cette gare. Sinon repli : A est la 1re gare listée.
    """
    obs = []
    for course in courses:
        ref, appels = course[0], course[1]
        origine = course[2] if len(course) > 2 else None
        for i, (a, b) in enumerate(troncons(appels, tri)):
            if ecarter_annules and (a.annule or b.annule):
                continue
            obs.append(Observation(
                recu, ref, a.gare, b.gare, a.dep_est, roulage_annonce(a, b), roulage_theorique(a, b),
                a_origine=(a.gare == origine) if origine else (i == 0), retard_dep=_secondes(a.dep_est, a.dep_theo),
                retard_arr_a=_secondes(a.arr_est, a.arr_theo), retard_arr_b=_secondes(b.arr_est, b.arr_theo),
                arret_est=_secondes(a.dep_est, a.arr_est), arret_theo=_secondes(a.dep_theo, a.arr_theo)))
    return obs


def une_par_course(observations):
    """Une observation par (course, tronçon) : le dernier relevé reçu avant le départ estimé de A.

    Retourne {'retenues': [...], 'ecartes': {'sans_annonce': n, 'depart_deja_passe': n}} (n = couples
    (course, tronçon) écartés).
    """
    groupes = {}
    for o in observations:
        groupes.setdefault((o.course, o.cle), []).append(o)
    retenues, sans_annonce, passe = [], 0, 0
    for obs in groupes.values():
        valides = [o for o in obs if o.annonce is not None and o.depart_est is not None]
        if not valides:
            sans_annonce += 1
            continue
        avant = [o for o in valides if o.recu <= o.depart_est]
        if not avant:
            passe += 1
            continue
        retenues.append(max(avant, key=lambda o: o.recu))
    return {"retenues": retenues, "ecartes": {"sans_annonce": sans_annonce, "depart_deja_passe": passe}}


def bilan_sous_borne(observations, bornes, retenus=None, origine=None):
    """Part des roulages annoncés strictement sous la borne. retenus = ensemble de (A, B) autorisés, ou None.

    origine = True : seulement les tronçons dont A est l'origine de la course ; False : seulement les autres ; None : tous.
    """
    b = Bilan()
    for o in observations:
        if o.annonce is None:
            continue
        if retenus is not None and o.cle not in retenus:
            continue
        if origine is not None and o.a_origine != origine:
            continue
        borne = borne_du_troncon(bornes, *o.cle)
        if borne is None:
            b.sans_borne += 1
            continue
        sous = 1 if o.annonce < borne else 0
        s, n = b.par_troncon.get(o.cle, (0, 0))
        b.par_troncon[o.cle] = (s + sous, n + 1)
        b.sous += sous
        b.total += 1
    return b


def categorie(part, bas=0.10, haut=0.90):
    """'bas' (presque 0 %), 'haut' (presque 100 %) ou 'mixte'. Seuils 10 % / 90 % : choix de travail."""
    if part <= bas:
        return "bas"
    if part >= haut:
        return "haut"
    return "mixte"


def mediane_theorique(observations, n_min=10):
    """{(A, B): (médiane du roulage théorique, n)} avec une valeur par course, tronçons à n >= n_min."""
    derniere = {}
    for o in observations:
        if o.theorique is None:
            continue
        k = (o.course, o.cle)
        if k not in derniere or o.recu > derniere[k].recu:
            derniere[k] = o
    valeurs = {}
    for (_, cle), o in derniere.items():
        valeurs.setdefault(cle, []).append(o.theorique)
    return {cle: (median(v), len(v)) for cle, v in valeurs.items() if len(v) >= n_min}


def rapports(medianes, realistes):
    """Rapports roulage théorique / roulage physique réaliste, un par tronçon ; realistes = {'a|b': s}."""
    out = []
    for (a, b), (m, _n) in medianes.items():
        r = realistes.get(f"{a}|{b}")
        if r is None:
            r = realistes.get(f"{b}|{a}")
        if r:
            out.append(m / r)
    return out


def stabilite(cats_a, cats_b):
    """Compare deux {(A, B): catégorie} sur leurs tronçons communs.

    Retourne (nb communs, nb identiques, [(tronçon, catégorie a, catégorie b)] des différents).
    """
    communs = sorted(set(cats_a) & set(cats_b))
    differents = [(c, cats_a[c], cats_b[c]) for c in communs if cats_a[c] != cats_b[c]]
    return len(communs), len(communs) - len(differents), differents


def mediane(valeurs):
    """Médiane ; nan si vide."""
    return median(valeurs) if valeurs else float("nan")


def moitie(observations, k):
    """Moitié k (0 ou 1) des observations, découpées par COURSE (crc32 de la référence, pair/impair).

    Même période, courses différentes : sert à mesurer le bruit d'échantillonnage des catégories.
    Une course reste toujours dans la même moitié.
    """
    if k not in (0, 1):
        raise ValueError("k doit valoir 0 ou 1")
    return [o for o in observations if crc32(o.course.encode()) % 2 == k]
