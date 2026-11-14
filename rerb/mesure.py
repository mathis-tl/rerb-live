"""Tâche 3 : erreur en mètres le long de la voie du niveau 1 et des repères 0a, 0b.

Cadrage : docs/cadrage-tache-3.md §13 et §14.
E1 : erreur = PK estimé moins PK du GPS projeté sur la voie, lue dans le SENS DE MARCHE du train : positif =
estimé en avance sur le train réel, négatif = en retard. Si le train roule vers les PK décroissants, on change le
signe (sinon « positif » voudrait dire « en retard »).
E3 : repère 0a = train placé à la prochaine gare ; 0b = à la dernière gare quittée ; niveau 1 = estimation.
E6 : un train à quai est mesuré à part (catégorie « à quai »).
Fonctions pures : `pks` = {gare: PK en mètres}, `pk_gps` = PK du GPS.
"""
import math
import statistics

from position import Etat

NIVEAUX = ("0a", "0b", "niveau1")


class Mesure:
    def __init__(self, categorie, erreurs):
        self.categorie = categorie  # "en route" ou "à quai"
        self.erreurs = erreurs      # {"0a", "0b", "niveau1"} -> mètres signés


def mesurer(estimation, pks, pk_gps):
    """-> (Mesure, None) ou (None, motif d'écart)."""
    if estimation.etat == Etat.A_QUAI:
        if estimation.gare not in pks:
            return None, "gare sans PK"
        # sens de marche inconnu à quai : signe sur l'axe des PK (la valeur absolue sert à la mesure)
        e = pks[estimation.gare] - pk_gps
        return Mesure("à quai", {n: e for n in NIVEAUX}), None
    if estimation.etat == Etat.EN_ROUTE:
        if estimation.gare not in pks or estimation.gare_suivante not in pks:
            return None, "gare sans PK"
        avant, apres = pks[estimation.gare], pks[estimation.gare_suivante]
        sens = 1.0 if apres >= avant else -1.0
        estime = avant + estimation.fraction * (apres - avant)
        return Mesure("en route", {"0a": sens * (apres - pk_gps), "0b": sens * (avant - pk_gps),
                                   "niveau1": sens * (estime - pk_gps)}), None
    return None, f"état : {estimation.etat.value}"


def resumer(erreurs):
    """Liste d'erreurs signées -> comptes et statistiques (E7 à valider : biais, médiane et 90e centile en valeur absolue)."""
    n = len(erreurs)
    if n == 0:
        return {"n": 0}
    absolues = sorted(abs(e) for e in erreurs)
    return {
        "n": n,
        "biais": statistics.fmean(erreurs),
        "mediane_abs": statistics.median(absolues),
        "p90_abs": absolues[math.ceil(0.9 * n) - 1],
    }
