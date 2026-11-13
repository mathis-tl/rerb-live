"""Mémoire M2 (décision P3, 13/11/2026) : garder la dernière heure connue des gares que PRIM retire.

Problème mesuré (docs/cadrage-tache-3.md §11) : PRIM retire de la liste d'un train les gares déjà
quittées ; sans mémoire, `estimer` répond « position inconnue » pendant 31 % du temps de roulage.

Règles :
• Une gare est « retirée » si elle figurait en tête de la liste du relevé précédent (avant la première
  gare encore présente). Une gare disparue au milieu de la liste (train devenu direct) est oubliée.
• Une gare retirée garde ses dernières heures connues et est marquée `reconstruit`.
• Réparation : le départ mémorisé d'une gare retirée ne peut pas dépasser l'arrivée (réestimée) de la
  gare suivante ; l'arrivée ne peut pas dépasser le départ réparé. Heure ramenée en arrière -> `repare`.
• Mémoire par train (clé fournie par l'appelant, par ex. DatedVehicleJourneyRef). Un relevé sans passage
  n'efface pas la mémoire.
"""
from dataclasses import replace


def _cle_temps(p):
    t = p.arrivee or p.depart
    return (t is None, t)


class Memoire:
    def __init__(self):
        self._precedent = {}  # clé du train -> passages consolidés du dernier relevé, triés par heure

    def consolider(self, cle, train):
        """Train du relevé courant + gares retirées gardées en mémoire -> Train consolidé."""
        if not train.passages:
            return train
        nouveaux = sorted(train.passages, key=_cle_temps)
        presentes = {p.gare for p in train.passages}
        retirees = []
        for i, p in enumerate(self._precedent.get(cle, [])):
            if p.gare in presentes:
                retirees = self._precedent[cle][:i]
                break
        # une gare annulée n'a pas été desservie : on ne la garde pas (sinon tout le train serait refusé)
        retirees = [replace(p, reconstruit=True) for p in retirees if not p.annule]
        suivante = nouveaux[0].arrivee or nouveaux[0].depart
        for i in range(len(retirees) - 1, -1, -1):
            p = retirees[i]
            depart, arrivee, change = p.depart, p.arrivee, False
            if suivante is not None:
                if depart is not None and depart > suivante:
                    depart, change = suivante, True
                limite = depart if depart is not None else suivante
                if arrivee is not None and arrivee > limite:
                    arrivee, change = limite, True
            retirees[i] = replace(p, arrivee=arrivee, depart=depart, repare=p.repare or change)
            suivante = arrivee if arrivee is not None else (depart if depart is not None else suivante)
        consolides = retirees + nouveaux
        self._precedent[cle] = consolides
        return type(train)(consolides, train.recorded_at)
