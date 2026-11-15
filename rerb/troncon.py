"""Tâche 4, J1 : latitude et longitude d'un train à X mètres de la gare A sur le tronçon A -> B.

Plan : docs/plan-tache-4.md (J1, décision Q2 option 1).
• chemin_troncon    : polyligne (lat, lon) de A à B. Gares voisines : `Geometry.chemin` (même candidat que la
                      distance utilisée par `estimer`). Gares non voisines (train semi-direct) : tronçons voisins
                      enchaînés le long du chemin unique de l'arbre (`Reseau.chemin`), sans point en double.
• point_sur_troncon : point à `distance_m` de A le long de cette polyligne (hors bornes : extrémité).
Fonctions pures, bibliothèque standard seulement. Les gares sont désignées par leur numéro (stops.py).
"""
from geometry import point_a_distance


def chemin_troncon(geometrie, reseau, a, b):
    gares = reseau.chemin(a, b)
    if len(gares) == 1:
        return geometrie.chemin(a, a)
    points = []
    for g1, g2 in zip(gares, gares[1:]):
        morceau = geometrie.chemin(g1, g2)
        points.extend(morceau if not points else morceau[1:])
    return points


def point_sur_troncon(geometrie, reseau, a, b, distance_m):
    return point_a_distance(chemin_troncon(geometrie, reseau, a, b), distance_m)
