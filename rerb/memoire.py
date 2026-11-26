"""Mémoire M2 (décision P3, 13/11/2026) : garder la dernière heure connue des gares que PRIM retire.

Problème mesuré : PRIM retire de la liste d'un train les gares déjà
quittées ; sans mémoire, `estimer` répond « position inconnue » pendant 31 % du temps de roulage.

Règles :
• Une gare est « retirée » si elle figurait en tête de la liste du relevé précédent (avant la première
  gare encore présente). Une gare disparue au milieu de la liste (train devenu direct) est oubliée.
• Une gare retirée garde ses dernières heures connues et est marquée `reconstruit`.
• Réparation : le départ mémorisé d'une gare retirée ne peut pas dépasser l'arrivée (réestimée) de la
  gare suivante ; l'arrivée ne peut pas dépasser le départ réparé. Heure ramenée en arrière -> `repare`.
• Mémoire par train (clé fournie par l'appelant, par ex. DatedVehicleJourneyRef). Un relevé sans passage
  n'efface pas la mémoire.
• Purge : une course absente du relevé depuis plus de PURGE_S (4 h) est oubliée ; une course sans
  horodatage n'a jamais été « vue » et n'est pas gardée. L'export ne dépasse pas TAILLE_MAX_OCTETS.
"""
import json
from dataclasses import fields, replace
from datetime import datetime

from position import Passage, Train

_INSTANTS = {"arrivee", "depart", "retenu_depuis", "retenu_le"}   # champs de Passage qui sont des instants (ISO 8601 en JSON)
VERSION_FORMAT = 1
PURGE_S = 4 * 3600                # une course non vue depuis 4 h est oubliée
TAILLE_MAX_OCTETS = 2_000_000     # au-delà, l'état n'est pas écrit (garde de taille du relayeur)


def _cle_temps(p):
    t = p.arrivee or p.depart
    return (t is None, t)


def passage_vers_json(p):
    return {f.name: (getattr(p, f.name).isoformat() if f.name in _INSTANTS and getattr(p, f.name) is not None
                     else getattr(p, f.name))
            for f in fields(Passage)}


def passage_depuis_json(d):
    return Passage(**{k: (datetime.fromisoformat(v) if k in _INSTANTS and v is not None else v) for k, v in d.items()})


def train_vers_json(train):
    recu = train.recorded_at
    return {"passages": [passage_vers_json(p) for p in train.passages],
            "recorded_at": recu.isoformat() if recu is not None else None}


def train_depuis_json(d):
    recu = d.get("recorded_at")
    return Train([passage_depuis_json(p) for p in d["passages"]],
                 datetime.fromisoformat(recu) if recu is not None else None)


class Memoire:
    def __init__(self):
        self._precedent = {}  # clé du train -> passages consolidés du dernier relevé, triés par heure
        self._vus = {}        # clé du train -> horodatage (recorded_at) du dernier relevé où elle figurait

    def exporter(self):
        """Passages retenus en JSON pur (instants en ISO 8601, format versionné)."""
        return {"v": VERSION_FORMAT,
                "trains": {cle: [passage_vers_json(p) for p in passages] for cle, passages in self._precedent.items()},
                "vus": {cle: v.isoformat() for cle, v in self._vus.items()}}

    def importer(self, d):
        """Remplace la mémoire par celle d'un `exporter()`. Sans « vus » (export plus ancien) : accepté."""
        if d.get("v") != VERSION_FORMAT:
            raise ValueError(f"format de mémoire inconnu : {d.get('v')!r}")
        self._precedent = {cle: [passage_depuis_json(p) for p in passages] for cle, passages in d["trains"].items()}
        self._vus = {cle: datetime.fromisoformat(v) for cle, v in d.get("vus", {}).items()}

    def taille_export(self):
        """Taille en octets de l'export JSON compact (ce qui est stocké avant compression)."""
        return len(json.dumps(self.exporter(), separators=(",", ":")).encode("utf-8"))

    def purger(self, maintenant):
        """Oublie les courses non vues depuis plus de PURGE_S, et celles qui n'ont jamais eu d'horodatage."""
        for cle in set(self._precedent) | set(self._vus):
            vu = self._vus.get(cle)
            if vu is None or (maintenant - vu).total_seconds() > PURGE_S:
                self._precedent.pop(cle, None)
                self._vus.pop(cle, None)

    def consolider(self, cle, train):
        """Train du relevé courant + gares retirées gardées en mémoire -> Train consolidé."""
        if train.recorded_at is not None:
            self._vus[cle] = train.recorded_at
        if not train.passages:
            return train
        # une gare listée sans aucune heure alors que la mémoire en a (MBTA : l'origine reste listée après le départ,
        # souvent avec un autre quai) : on garde les heures mémorisées, marquées `reconstruit` (revue R1, point 1)
        connues = {p.gare: p for p in self._precedent.get(cle, [])}
        passages = [replace(connues[p.gare], reconstruit=True)
                    if p.arrivee is None and p.depart is None and not p.annule and p.gare in connues else p
                    for p in train.passages]
        nouveaux = sorted(passages, key=_cle_temps)
        presentes = {p.gare for p in passages}
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
