"""Lissage des changements d'heure d'arrivée annoncés par PRIM (étape 3 de docs/plan-sejour-quai.md, décisions de Mathis du 18/11).

Problème : PRIM revoit souvent l'heure d'arrivée d'un train (49 changements en une heure à Palaiseau, 45 de plus de 30 s). Appliquée telle
quelle, une révision fait sauter le train sur la carte ou change sa vitesse brutalement.

Règle : pour chaque (course, gare), on garde en mémoire l'arrivée « adoptée ».
• Nouvelle heure PRIM à TOLERANCE_S (15 s) ou moins de l'adoptée : on garde l'adoptée (écart toléré, la vitesse ne change pas).
• Sinon l'adoptée se rapproche de la zone de ±TOLERANCE_S autour de l'heure PRIM, au plus RAMPE_MAX seconde par seconde écoulée
  (la durée d'un tronçon varie alors de 20 % au plus : pas de saut de position).
• Le décalage (adoptée - PRIM) s'applique à l'arrivée ET au départ du passage.
• Un passage dont l'arrivée adoptée est dépassée (le train est arrivé) est figé : sinon le départ, donc la position, sauterait.
• Révision vers plus tard de plus de RETENUE_S (120 s) alors que le train roule : « retenue » (décision du 21/11). L'arrivée adoptée suit PRIM tout de suite ; le passage
  porte retenu_depuis (ancienne arrivée adoptée) et retenu_le (instant de la révision), et position.estimer arrête le train en pleine voie avant la gare.
• Garde-fou : le lissage ne doit jamais rendre un train « non placé » (arrivée avant le départ précédent) ; on repousse alors l'heure.

Le Lisseur est la seule mémoire du serveur sur les relevés passés. Il retient aussi, par course, si PRIM a déjà modifié une heure
depuis la première observation (sert à l'étiquette « Horaire théorique »). Accès protégés par un verrou (serveur multi-fils).
"""
import threading
from dataclasses import replace
from datetime import timedelta

from position import depart_effectif

TOLERANCE_S = 15.0   # écart entre l'arrivée adoptée et l'heure PRIM en dessous duquel on ne change rien (Mathis, 18/11 : 30 s, puis 15 s après le rejeu : erreur de départ 54 s au lieu de 59 s)
BIAIS_ARRIVEE_S = 10.0   # le train arrive en moyenne plus tard que l'heure estimée par PRIM (relevé de Palaiseau, 18/11 : 26 s, 12 trains) : on ajoute 10 s à chaque arrivée PRIM, valeur de départ décidée par Mathis, à réviser avec un 2e relevé
RETENUE_S = 120.0    # révision PRIM vers plus tard de plus de 120 s (pendant que le train roule) : le train est retenu en pleine voie au lieu de ralentir (= ARRET_ANNONCE_MAX)
RAMPE_MAX = 0.2      # secondes d'arrivée rattrapées par seconde écoulée : la vitesse du train varie de 20 % au plus


class Lisseur:
    def __init__(self, tolerance_s=TOLERANCE_S, rampe=RAMPE_MAX, biais_s=BIAIS_ARRIVEE_S, retenue_s=RETENUE_S):
        self._retenue = timedelta(seconds=retenue_s)
        self._retenues = {}   # (course, gare) -> (arrivée adoptée avant la révision, instant de la révision)
        self._tolerance = timedelta(seconds=tolerance_s)
        self._biais = timedelta(seconds=biais_s)
        self._rampe = rampe
        self._verrou = threading.Lock()
        self._adoptees = {}   # (course, gare) -> [arrivée adoptée, instant de la dernière mise à jour]
        self._courses = {}    # course -> {"premiere": instant du 1er relevé PRIM, "derniere": instant du dernier relevé PRIM, "modifie": bool, "vues": {...}}

    def purger(self, courses_presentes):
        """Oublie les courses qui ne sont plus dans le relevé (elles repartiraient de la valeur PRIM si elles revenaient)."""
        presentes = set(courses_presentes)
        with self._verrou:
            for cle in [k for k in self._adoptees if k[0] not in presentes]:
                del self._adoptees[cle]
            for cle in [k for k in self._retenues if k[0] not in presentes]:
                del self._retenues[cle]
            for ref in [r for r in self._courses if r not in presentes]:
                del self._courses[ref]

    def modifie(self, ref):
        """PRIM a-t-il déjà changé une heure de cette course depuis la première observation ? (False si course inconnue)"""
        with self._verrou:
            c = self._courses.get(ref)
            return bool(c and c["modifie"])

    def adoptee(self, ref, gare):
        """Arrivée actuellement adoptée pour (course, gare), ou None (pour les tests et l'audit)."""
        with self._verrou:
            etat = self._adoptees.get((ref, gare))
            return etat[0] if etat else None

    def suivi_s(self, ref):
        """Durée de suivi d'une course, comptée en RELEVÉS PRIM (instant de réception du premier au dernier relevé qui la contient) et non en
        requêtes : si PRIM tombe, le même relevé est servi indéfiniment et la durée ne grandit pas. 0 si course inconnue."""
        with self._verrou:
            c = self._courses.get(ref)
            return (c["derniere"] - c["premiere"]).total_seconds() if c else 0.0

    def appliquer(self, ref, train, maintenant, recu=None):
        """Train dont les heures sont lissées (le même objet s'il n'y a rien à changer). `recu` : instant de réception du relevé PRIM
        (défaut : `maintenant`) ; il date le suivi de la course."""
        recu = recu if recu is not None else maintenant
        with self._verrou:
            course = self._courses.setdefault(ref, {"premiere": recu, "derniere": recu, "modifie": False, "vues": {}})
            course["derniere"] = max(course["derniere"], recu)
            for p in train.passages:
                for genre, valeur in (("a", p.arrivee), ("d", p.depart)):
                    if valeur is None:
                        continue
                    premiere = course["vues"].setdefault((p.gare, genre), valeur)
                    if premiere != valeur:
                        course["modifie"] = True
            nouveaux = []
            for p in train.passages:
                if p.arrivee is None:
                    nouveaux.append(p)
                    continue
                decalage = self._adopter(ref, p.gare, p.arrivee + self._biais, maintenant) - p.arrivee   # la tolérance s'applique autour de l'heure biaisée
                if decalage:
                    p = replace(p, arrivee=p.arrivee + decalage, depart=None if p.depart is None else p.depart + decalage)
                retenue = self._retenues.get((ref, p.gare))
                if retenue and p.arrivee > maintenant:
                    p = replace(p, retenu_depuis=retenue[0], retenu_le=retenue[1])
                nouveaux.append(p)
            if any(a is not b for a, b in zip(train.passages, nouveaux)):
                nouveaux = self._garantir_coherence(train.passages, nouveaux)
        if all(a is b for a, b in zip(train.passages, nouveaux)):
            return train
        return replace(train, passages=nouveaux)

    def _adopter(self, ref, gare, prim, maintenant):
        etat = self._adoptees.get((ref, gare))
        if etat is None:
            self._adoptees[(ref, gare)] = [prim, maintenant]
            return prim
        adoptee, derniere = etat
        if adoptee > maintenant:   # le train n'est pas encore arrivé : l'adoptée peut encore bouger
            ecart = prim - adoptee
            pas = timedelta(seconds=self._rampe * max(0.0, (maintenant - derniere).total_seconds()))
            retenue = self._retenues.get((ref, gare))
            if retenue:   # déjà retenu : le train est à l'arrêt, il suit PRIM (au-delà de la tolérance), sans jamais arriver avant l'ancienne heure
                if abs(ecart) > self._tolerance:
                    adoptee = max(prim, retenue[0])
            elif ecart > self._retenue:
                self._retenues[(ref, gare)] = (adoptee, maintenant)
                adoptee = prim
            elif ecart > self._tolerance:
                adoptee = min(adoptee + pas, prim - self._tolerance)
            elif ecart < -self._tolerance:
                adoptee = max(adoptee - pas, prim + self._tolerance)
        etat[0], etat[1] = adoptee, maintenant
        return adoptee

    @staticmethod
    def _garantir_coherence(anciens, nouveaux):
        """Le lissage ne doit jamais rendre un train « non placé » : une arrivée qui suivait le départ EFFECTIF (séjour raccourci, voir
        position.depart_effectif) de la gare d'avant dans les heures PRIM ne doit pas le précéder après lissage. Une incohérence déjà présente
        chez PRIM n'est pas touchée (estimer() la juge, et sa règle de séjour peut l'absorber). Ordre = celui des heures PRIM d'origine."""
        if any(p.arrivee is None and p.depart is None for p in anciens):
            return nouveaux   # heures manquantes : estimer() refusera ce train de toute façon
        ordre = sorted(range(len(nouveaux)), key=lambda i: anciens[i].arrivee or anciens[i].depart)
        sortie = list(nouveaux)
        dernier = ordre[-1]

        def depart_de(p, i):
            if p.depart is None:
                return None
            return p.depart if (i == dernier or p.arrivee is None) else depart_effectif(p.gare, p.arrivee, p.depart)
        depart_ancien = depart_nouveau = None
        for i in ordre:
            p = sortie[i]
            if (depart_nouveau is not None and p.arrivee is not None and p.arrivee < depart_nouveau
                    and not (depart_ancien is not None and anciens[i].arrivee is not None and anciens[i].arrivee < depart_ancien)):
                p = replace(p, arrivee=depart_nouveau, depart=None if p.depart is None else max(p.depart, depart_nouveau))
                sortie[i] = p
            d_nouveau, d_ancien = depart_de(p, i), depart_de(anciens[i], i)
            if d_nouveau is not None:
                depart_nouveau = d_nouveau
            if d_ancien is not None:
                depart_ancien = d_ancien
        return sortie
