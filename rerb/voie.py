"""Distance le long d'une voie unique (Boston, Helsinki) et PK d'un point (gare ou GPS).

PK = point kilométrique : distance en mètres depuis le début de la voie, en la suivant.
Un train de Boston ou d'Helsinki suit UNE forme de voie (shapes.txt) : la distance entre deux gares est la
différence de leurs PK, sans la règle « minimum valide » du RER B (qui a 49 morceaux qui se recouvrent).

Qui fait quoi :
• Voie                  : une voie (liste de (lat, lon)) ; candidats(lat, lon), pk(lat, lon, apres).
• pk_gares              : PK des gares d'un train, dans l'ordre du train.
• distance_sur_voie     : {gare: PK} -> fonction distance(a, b) pour estimer() de position.py.
• charger_formes/trips/arrets : lecture des fichiers GTFS statiques (shapes.txt, trips.txt, stops.txt).

Décision P1 (13/11/2026) : au-delà de 150 m de la voie, un point est rejeté (PointHorsVoie) et compté.
Boucle : si la voie passe deux fois près d'un point, `pk` sans indice lève PkAmbigu ; avec `apres` (PK du
point précédent du train), on prend le premier passage situé au-delà. Pas de nettoyage des aller-retours
ici (inutile tant qu'on n'en a pas vu dans ces formes).
"""
import csv
from pathlib import Path

from geometry import cumulative, station_passes
from position import DistanceInconnue


class PointHorsVoie(ValueError):
    """Aucun passage de la voie à moins du rayon (ou aucun après le PK demandé)."""


class PkAmbigu(ValueError):
    """La voie passe plusieurs fois près du point et aucun indice n'a été donné."""


class Voie:
    def __init__(self, points, rayon=150.0):
        """points : liste de (lat, lon) dans l'ordre de la voie."""
        if len(points) < 2:
            raise ValueError("une voie a au moins 2 points")
        self._points = list(points)
        self._cum = cumulative(self._points)
        self._rayon = rayon

    @property
    def longueur(self):
        return self._cum[-1]

    def candidats(self, lat, lon):
        """Passages (PK, écart à la voie) à moins du rayon, triés par PK."""
        return sorted(station_passes(self._points, self._cum, lat, lon, self._rayon), key=lambda p: p.pk)

    def pk(self, lat, lon, apres=None):
        """PK du point. `apres` : PK minimum (on prend le premier passage au-delà)."""
        c = self.candidats(lat, lon)
        if not c:
            raise PointHorsVoie(f"à plus de {self._rayon:.0f} m de la voie : ({lat:.5f}, {lon:.5f})")
        if apres is None:
            if len(c) > 1:
                raise PkAmbigu(f"{len(c)} passages près de ({lat:.5f}, {lon:.5f}) : "
                               f"PK {[round(p.pk) for p in c]}")
            return c[0].pk
        suivants = [p for p in c if p.pk >= apres]
        if not suivants:
            raise PointHorsVoie(f"aucun passage après le PK {apres:.0f} près de ({lat:.5f}, {lon:.5f})")
        return suivants[0].pk


def pk_gares(voie, gares, premier_passage=False):
    """gares : [(identifiant, lat, lon)] dans l'ordre du train -> [(identifiant, PK)].
    Chaque gare prend le premier passage de la voie au-delà de la gare précédente.
    La 1re gare, si la voie passe plusieurs fois près d'elle : refus (PkAmbigu) par défaut ;
    avec premier_passage=True (décision P2, 13/11/2026, HSL lignes en boucle) on prend le premier passage."""
    resultat, precedent = [], None
    for i, (ident, lat, lon) in enumerate(gares):
        if i == 0 and premier_passage:
            precedent = 0.0
        precedent = voie.pk(lat, lon, apres=precedent)
        resultat.append((ident, precedent))
    return resultat


def distance_sur_voie(pks):
    """pks : {gare: PK} -> distance(a, b) = différence de PK. Lève DistanceInconnue si une gare manque."""
    def distance(a, b):
        for g in (a, b):
            if g not in pks:
                raise DistanceInconnue(f"gare sans PK : {g}")
        return abs(pks[b] - pks[a])
    return distance


def charger_formes(chemin, ids=None):
    """shapes.txt (GTFS) -> {shape_id: [(lat, lon)]}, dans l'ordre shape_pt_sequence. `ids` filtre."""
    voulus = None if ids is None else set(ids)
    brut = {}
    with Path(chemin).open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if voulus is not None and r["shape_id"] not in voulus:
                continue
            brut.setdefault(r["shape_id"], []).append(
                (int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))
    return {sid: [(lat, lon) for _, lat, lon in sorted(pts)] for sid, pts in brut.items()}


def charger_trips(chemin):
    """trips.txt (GTFS) -> {trip_id: shape_id}."""
    with Path(chemin).open(newline="", encoding="utf-8") as f:
        return {r["trip_id"]: r["shape_id"] for r in csv.DictReader(f)}


def charger_arrets(chemin):
    """stops.txt (GTFS) -> {stop_id: (lat, lon)}."""
    with Path(chemin).open(newline="", encoding="utf-8") as f:
        return {r["stop_id"]: (float(r["stop_lat"]), float(r["stop_lon"]))
                for r in csv.DictReader(f) if r["stop_lat"] and r["stop_lon"]}
