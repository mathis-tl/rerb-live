"""Distance le long des rails entre deux gares du RER B.

Qui fait quoi :
• load_morceaux    : lit le tracé IDFM (49 morceaux) -> listes de points (lat, lon).
• clean_retraces   : efface les aller-retours parasites du dessin (« pointes »).
• cumulative       : distance cumulée le long d'une ligne de points.
• station_passes   : tous les passages d'une ligne près d'une gare, par projection sur le segment.
• choose_distance  : règle « minimum valide » entre candidats.
• Geometry         : distance(a, b), straight(a, b), neighbor_pairs() sur l'ensemble des morceaux.

Règle de choix (mesures du 12/11/2026) : parmi tous les
candidats (un par paire de passages, sur tous les morceaux), on garde le plus petit qui est entre
`low` x ligne droite et `high` x ligne droite. Seuils 0,99 et 1,35 : provisoires, réglables.
Bibliothèque standard seulement.
"""
import json
import math
from bisect import bisect_right
from collections import namedtuple
from pathlib import Path

_R = 6371000.0
Pass = namedtuple("Pass", "pk offset")  # pk : mètres depuis le début de la ligne ; offset : mètres de la gare à la voie


class NoValidDistance(ValueError):
    """Aucune distance plausible pour ce couple de gares."""


def _dist(p, q):
    """Distance en mètres entre deux points (lat, lon) proches."""
    x = math.radians(q[1] - p[1]) * math.cos(math.radians((p[0] + q[0]) / 2))
    y = math.radians(q[0] - p[0])
    return _R * math.hypot(x, y)


def load_morceaux(trace_path):
    """Tracé IDFM (JSON de l'API open data) -> liste de morceaux, chacun une liste de (lat, lon)."""
    data = json.loads(Path(trace_path).read_text(encoding="utf-8"))
    shape = data["results"][0]["shape"]
    geom = shape.get("geometry", shape)
    return [[(lat, lon) for lon, lat in line] for line in geom["coordinates"]]


def cumulative(points):
    """Distance cumulée (m) le long de la ligne, un nombre par point, le premier vaut 0."""
    cum = [0.0]
    for p, q in zip(points, points[1:]):
        cum.append(cum[-1] + _dist(p, q))
    return cum


def clean_retraces(points, tolerance=10.0, min_gap=20.0):
    """Supprime les retours en arrière : un point qui retombe à moins de `tolerance` m d'un point déjà
    parcouru (au moins `min_gap` m de chemin plus tôt) efface tout ce qui a été parcouru entre les deux."""
    if not points:
        return []
    k1 = _R * math.pi / 180
    k0 = k1 * math.cos(math.radians(points[0][0]))
    pile, xs, ys, cum, grid = [], [], [], [], {}
    for p in points:
        x, y = p[1] * k0, p[0] * k1
        cx, cy = int(x // tolerance), int(y // tolerance)
        best = None
        if cum:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in grid.get((cx + dx, cy + dy), ()):
                        if (cum[-1] - cum[j] > min_gap
                                and math.hypot(x - xs[j], y - ys[j]) < tolerance
                                and (best is None or j < best)):
                            best = j
        if best is not None:
            while len(pile) - 1 > best:
                k = len(pile) - 1
                grid[(int(xs[k] // tolerance), int(ys[k] // tolerance))].remove(k)
                pile.pop(); xs.pop(); ys.pop(); cum.pop()
            continue
        cum.append(cum[-1] + math.hypot(x - xs[-1], y - ys[-1]) if pile else 0.0)
        grid.setdefault((cx, cy), []).append(len(pile))
        pile.append(p); xs.append(x); ys.append(y)
    return pile


def _point_au_pk(points, cum, pk):
    """Point (lat, lon) à `pk` mètres du début de la ligne (interpolation linéaire sur le segment)."""
    pk = max(0.0, min(pk, cum[-1]))
    i = min(bisect_right(cum, pk) - 1, len(points) - 2)
    seg = cum[i + 1] - cum[i]
    t = 0.0 if seg == 0 else (pk - cum[i]) / seg
    return (points[i][0] + t * (points[i + 1][0] - points[i][0]),
            points[i][1] + t * (points[i + 1][1] - points[i][1]))


def extraire(points, cum, pk_debut, pk_fin):
    """Sous-ligne entre deux PK, dans l'ordre pk_debut -> pk_fin (retournée si pk_debut > pk_fin)."""
    bas, haut = sorted((pk_debut, pk_fin))
    sous = [_point_au_pk(points, cum, bas)]
    sous += [p for p, c in zip(points, cum) if bas < c < haut]
    sous.append(_point_au_pk(points, cum, haut))
    return sous if pk_debut <= pk_fin else sous[::-1]


def point_a_distance(chemin, distance):
    """Point (lat, lon) à `distance` mètres du début d'une polyligne ; hors bornes : extrémité la plus proche."""
    cum = cumulative(chemin)
    if len(chemin) == 1:
        return chemin[0]
    return _point_au_pk(chemin, cum, distance)


def station_passes(points, cum, lat, lon, radius=150.0):
    """Passages de la ligne à moins de `radius` m de la gare, par projection sur les segments.
    Les segments voisins forment un seul passage (on garde le plus proche)."""
    k1 = _R * math.pi / 180
    k0 = k1 * math.cos(math.radians(lat))
    found = []  # (indice de segment, distance gare-voie, pk)
    for i in range(len(points) - 1):
        ax, ay = (points[i][1] - lon) * k0, (points[i][0] - lat) * k1
        bx, by = (points[i + 1][1] - lon) * k0, (points[i + 1][0] - lat) * k1
        vx, vy = bx - ax, by - ay
        l2 = vx * vx + vy * vy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * vx + ay * vy) / l2))
        off = math.hypot(ax + t * vx, ay + t * vy)
        if off <= radius:
            found.append((i, off, cum[i] + t * (cum[i + 1] - cum[i])))
    groups, cur = [], []
    for item in found:
        if cur and item[0] - cur[-1][0] > 3:
            groups.append(cur)
            cur = []
        cur.append(item)
    if cur:
        groups.append(cur)
    return [Pass(pk=min(g, key=lambda r: r[1])[2], offset=min(g, key=lambda r: r[1])[1]) for g in groups]


def choose_distance(candidates, straight, low=0.99, high=1.35):
    """Plus petit candidat entre low x straight et high x straight. Lève NoValidDistance sinon."""
    valid = [c for c in candidates if low * straight <= c <= high * straight]
    if not valid:
        raise NoValidDistance(
            f"aucun candidat entre {low * straight:.0f} m et {high * straight:.0f} m "
            f"(ligne droite {straight:.0f} m, candidats {sorted(round(c) for c in candidates)[:10]})")
    return min(valid)


class Geometry:
    def __init__(self, morceaux, stations, radius=150.0, low=0.99, high=1.35,
                 tolerance=10.0, min_gap=20.0):
        """morceaux : liste de listes de (lat, lon) ; stations : {identifiant: (lat, lon)}."""
        self._stations = dict(stations)
        self._radius, self._low, self._high = radius, low, high
        self._lines = []
        for m in morceaux:
            pts = clean_retraces(m, tolerance, min_gap)
            self._lines.append((pts, cumulative(pts)))
        self._passes = {}

    def _passes_of(self, station):
        if station not in self._passes:
            lat, lon = self._stations[station]  # KeyError si gare inconnue
            self._passes[station] = [station_passes(p, c, lat, lon, self._radius) for p, c in self._lines]
        return self._passes[station]

    def straight(self, a, b):
        """Distance à vol d'oiseau entre deux gares (m)."""
        return _dist(self._stations[a], self._stations[b])

    def distance(self, a, b):
        """Distance le long des rails entre deux gares (m), sans orientation."""
        if a == b:
            self._stations[a]
            return 0.0
        straight = self.straight(a, b)
        pa, pb = self._passes_of(a), self._passes_of(b)
        candidates = [abs(y.pk - x.pk) for xs, ys in zip(pa, pb) for x in xs for y in ys]
        return choose_distance(candidates, straight, self._low, self._high)

    def chemin(self, a, b):
        """Polyligne (lat, lon) de a vers b le long des rails, sur le MÊME candidat que `distance`
       . Lève KeyError (gare inconnue) ou NoValidDistance (aucun candidat plausible)."""
        if a == b:
            return [self._stations[a]]
        straight = self.straight(a, b)
        pa, pb = self._passes_of(a), self._passes_of(b)
        candidates = [(abs(y.pk - x.pk), i, x.pk, y.pk)
                      for i, (xs, ys) in enumerate(zip(pa, pb)) for x in xs for y in ys]
        choose_distance([c[0] for c in candidates], straight, self._low, self._high)  # lève si aucun valide
        valid = [c for c in candidates if self._low * straight <= c[0] <= self._high * straight]
        _, i, pk_a, pk_b = min(valid, key=lambda c: c[0])
        points, cum = self._lines[i]
        return extraire(points, cum, pk_a, pk_b)

    def point_quai(self, station):
        """Point (lat, lon) SUR la voie, au plus près des coordonnées de la gare (passage de plus petit décalage, tous morceaux
        confondus). Point unique par gare : sert de quai commun à la fin du tronçon qui arrive et au début de celui qui repart.
        Lève KeyError (gare inconnue) ou NoValidDistance (aucun passage à moins du rayon)."""
        candidats = [(pas.offset, point_a_distance(points, pas.pk))
                     for (points, cum), passages in zip(self._lines, self._passes_of(station)) for pas in passages]
        if not candidats:
            raise NoValidDistance(f"aucun passage de la ligne à moins de {self._radius:.0f} m de la gare {station}")
        # Le passage le plus proche des coordonnées de la gare peut être une autre voie (Massy - Palaiseau : 53 m sur un morceau isolé,
        # alors que presque tous les passages, donc tous les tronçons voisins, sont à ~90 m sur la voie principale). On prend donc le
        # passage le plus CENTRAL (médoïde : plus petite somme des distances aux autres), à égalité le plus proche de la gare.
        return min(candidats, key=lambda c: (round(sum(_dist(c[1], autre[1]) for autre in candidats), 3), c[0]))[1]

    def neighbor_pairs(self):
        """Couples de gares voisines (consécutives sur un morceau), sans orientation, triés."""
        pairs = set()
        for i in range(len(self._lines)):
            ordre = sorted((p.pk, s) for s in self._stations for p in self._passes_of(s)[i])
            for (_, sa), (_, sb) in zip(ordre, ordre[1:]):
                if sa != sb:
                    pairs.add(tuple(sorted((sa, sb))))
        return sorted(pairs)
