"""Tâche 4, T1 : durée minimale plausible par tronçon (gares voisines du RER B). Fonctions pures, stdlib seulement.

PRIM annonce parfois des trajets impossibles (Massy-Palaiseau -> Palaiseau, 1 435 m, en 30 s ou 60 s). Une limite unique de
33 m/s ne les voit pas tous (1 435 m en 60 s = 24 m/s). On mesure donc, tronçon par tronçon, la durée réellement observée
et on en déduit une durée minimale plausible. Valeur marquée « correction estimée » : aucune vérité terrain hors VehicleAtStop.

Vocabulaire
• tronçon       : couple de gares voisines, sans orientation (`cle_troncon`).
• observé       : départ de A (VehicleAtStop vrai puis faux, `verite.departs_observes`) jusqu'à l'arrivée à B (course listée sans
                  être à quai, puis à quai, `arrivees_observees`). Chaque événement est un intervalle entre deux relevés
                  (au plus ECART_MAX) ; on prend son milieu. Incertitude (borne maximale) = demi-somme des deux largeurs d'intervalle.
• annoncé       : heure d'arrivée à B moins heure de départ de A, telles que PRIM les annonçait (calculé par le script).
• plancher      : distance / 33 m/s (RER B limité à 120 km/h). Repli pour un tronçon inconnu ou trop peu observé.
• durée minimale plausible : max(plancher, bas centile des durées observées valides) si au moins N_MIN observations, sinon plancher.
Ralentissement réel (incident : observé long aussi) et erreur de donnée (annoncé trop court, observé normal) sont distingués par `classer`.
"""
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from stops import StopNotFound, stop_area_number

PLANCHER_VITESSE_MS = 33.0
ECART_MAX = timedelta(seconds=45)   # même seuil que J2 bis (relevé /30 s + gigue)
N_MIN = 5                           # observations valides minimales pour fier la durée minimale à l'observé
CENTILE = 10                        # « bas centile »
FACTEUR_LONG = 1.5                  # observé ou annoncé au-delà de 1,5 × la médiane du tronçon : « long »
PART_IMPOSSIBLES_MAX = 0.30  # au-delà, observations trop bruitées : minimum neutralisé (retour au plancher)
DUREE_MAX_S = 900                   # un tronçon de plus de 15 min : arrivée sans rapport avec ce départ

R_TROU = "trou de relevés trop long"
R_APPARU = "arrivée non vue avant (train apparu à quai)"
R_SANS_ARRIVEE = "aucune arrivée observée ensuite"
R_MANQUEE = "arrivée manquée (départ suivant observé avant)"
R_NON_VOISINE = "arrivée à une gare non voisine (gare sautée ou arrivée manquée)"
R_AVANT = "arrivée avant le départ"
R_TROP_LONG = "arrivée trop tardive (plus de 15 min)"

OBS_IMPOSSIBLE = "observation impossible"
NORMAL = "normal"
ERREUR_DONNEE = "erreur de donnée (annoncé trop court, observé normal)"
AMBIGU = "ambigu (annoncé trop court, observé long)"
RALENTI_ANNONCE = "ralentissement annoncé"
RALENTI_NON_ANNONCE = "ralentissement non annoncé"
SANS_ANNONCE = "annonce non lue"


@dataclass(frozen=True)
class Observation:
    course: str
    de: str
    vers: str
    duree_s: float          # observée (milieux des intervalles)
    incertitude_s: float    # borne maximale : demi-somme des largeurs des deux intervalles
    instant: datetime       # milieu de l'intervalle du départ
    annonce_s: "float | None" = None


def cle_troncon(a, b):
    return tuple(sorted((a, b)))


def centile(valeurs, p):
    """Centile p (0 à 100), interpolation linéaire. None si vide."""
    if not valeurs:
        return None
    v = sorted(valeurs)
    k = (len(v) - 1) * p / 100.0
    bas = math.floor(k)
    haut = min(bas + 1, len(v) - 1)
    return v[bas] + (v[haut] - v[bas]) * (k - bas)


def arrivees_observees(releves, ecart_max=ECART_MAX):
    """releves : [(instant, courses listées, courses à quai)] d'UNE gare, ordre croissant.
    -> ([(course, bas, haut)], Counter des refus). Arrivée = listée sans être à quai au relevé t1, à quai au relevé t2."""
    arrivees, refus = [], Counter()
    for (t1, listees1, quai1), (t2, _, quai2) in zip(releves, releves[1:]):
        for c in sorted(quai2 - quai1):
            if t2 - t1 > ecart_max:
                refus[R_TROU] += 1
            elif c not in listees1:
                refus[R_APPARU] += 1
            else:
                arrivees.append((c, t1, t2))
    return arrivees, refus


def _milieu(bas, haut):
    return bas + (haut - bas) / 2


def apparier_troncons(departs, arrivees, voisin):
    """departs : [(gare, course, bas, haut)] ; arrivees : [(gare, course, bas, haut)] ; voisin(a, b) -> bool.
    Chaque départ est apparié avec l'événement suivant de la même course : il doit être une arrivée à une gare voisine.
    -> ([Observation], Counter des refus)."""
    par_course = {}
    for g, c, bas, haut in departs:
        par_course.setdefault(c, []).append((_milieu(bas, haut), 1, "départ", g, bas, haut))
    for g, c, bas, haut in arrivees:
        par_course.setdefault(c, []).append((_milieu(bas, haut), 0, "arrivée", g, bas, haut))
    observations, refus = [], Counter()
    for course, evenements in par_course.items():
        evenements.sort(key=lambda e: (e[0], e[1]))
        for i, (m_dep, _, genre, gare, bas, haut) in enumerate(evenements):
            if genre != "départ":
                continue
            if i + 1 >= len(evenements):
                refus[R_SANS_ARRIVEE] += 1
                continue
            m_arr, _, genre2, gare2, bas2, haut2 = evenements[i + 1]
            if genre2 == "départ":
                refus[R_MANQUEE] += 1
            elif gare2 == gare or not voisin(gare, gare2):
                refus[R_NON_VOISINE] += 1
            elif m_arr <= m_dep:
                refus[R_AVANT] += 1
            elif (m_arr - m_dep).total_seconds() > DUREE_MAX_S:
                refus[R_TROP_LONG] += 1
            else:
                incertitude = ((haut - bas) + (haut2 - bas2)).total_seconds() / 2.0
                observations.append(Observation(course, gare, gare2, (m_arr - m_dep).total_seconds(), incertitude, m_dep))
    return observations, refus


def valide(observation, plancher_s):
    """Une durée observée sous le plancher (distance / 33 m/s) est une erreur d'observation, pas une durée : refusée et comptée.
    On juge le milieu, pas la borne haute : avec ±30 s d'incertitude, la borne haute laisserait tout passer."""
    return observation.duree_s >= plancher_s


def duree_min_plausible(durees, plancher_s, centile_p=CENTILE, n_min=N_MIN):
    """-> (durée minimale en s, source « estimée » ou « plancher », nombre de durées)."""
    n = len(durees)
    if n >= n_min:
        c = centile(durees, centile_p)
        if c > plancher_s:
            return c, "estimée", n
    return plancher_s, "plancher", n


def classer(observation, duree_min_s, plancher_s, mediane_s, facteur_long=FACTEUR_LONG):
    """Classe une observation (annonce de PRIM comprise) pour ne pas confondre ralentissement réel et erreur de donnée."""
    if not valide(observation, plancher_s):
        return OBS_IMPOSSIBLE
    if observation.annonce_s is None:
        return SANS_ANNONCE
    observe_long = observation.duree_s > facteur_long * mediane_s
    annonce_courte = observation.annonce_s < duree_min_s
    if annonce_courte:
        return AMBIGU if observe_long else ERREUR_DONNEE
    if observe_long:
        return RALENTI_ANNONCE if observation.annonce_s > facteur_long * mediane_s else RALENTI_NON_ANNONCE
    return NORMAL


def table_duree_min(observations, distances, vitesse_ms=PLANCHER_VITESSE_MS, centile_p=CENTILE, n_min=N_MIN):
    """distances : {cle_troncon: mètres}. -> {cle_troncon: {distance_m, plancher_s, duree_min_s, source, n, mediane_s}}.
    Tous les tronçons de `distances` figurent, même sans observation (alors : plancher)."""
    par_troncon = {}
    for o in observations:
        par_troncon.setdefault(cle_troncon(o.de, o.vers), []).append(o)
    table = {}
    for cle, d in distances.items():
        plancher = d / vitesse_ms
        toutes = par_troncon.get(cle, [])
        durees = [o.duree_s for o in toutes if valide(o, plancher)]
        part = (len(toutes) - len(durees)) / len(toutes) if toutes else 0.0
        if part > PART_IMPOSSIBLES_MAX:
            v, source, n = plancher, "plancher", len(durees)  # drapeau trop bruité : on ne corrige pas au-delà du plancher
        else:
            v, source, n = duree_min_plausible(durees, plancher, centile_p, n_min)
        table[cle] = {"distance_m": d, "plancher_s": plancher, "duree_min_s": v, "source": source, "n": n,
                      "mediane_s": centile(durees, 50), "part_impossibles": part}
    return table


def fournisseur_duree_min(chemin):
    """Lit la table écrite par exploration/mesurer_duree_troncon.py -> f(ref_gare_a, ref_gare_b) = (secondes, source) ou None.
    Références PRIM « STIF:StopArea:SP:<n>: ». None = tronçon inconnu (ou fichier absent, illisible) : l'appelant se replie sur
    le plancher distance / 33 m/s."""
    try:
        with open(chemin, encoding="utf-8") as f:
            troncons = json.load(f)["troncons"]
    except (OSError, ValueError, KeyError, TypeError):
        troncons = {}

    def duree_min(ref_a, ref_b):
        try:
            a, b = cle_troncon(stop_area_number(ref_a), stop_area_number(ref_b))
        except StopNotFound:
            return None
        try:
            t = troncons.get(f"{a}|{b}")
            return (float(t["duree_min_s"]), t["source"]) if t else None
        except (AttributeError, KeyError, TypeError, ValueError):
            return None  # entrée mal formée : repli sur le plancher, jamais d'erreur 500

    return duree_min
