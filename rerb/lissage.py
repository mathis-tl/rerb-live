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

Deux temps (T3, 24/11/2026), pour que plusieurs copies du serveur donnent le même résultat quelle que soit la cadence des requêtes :
• `avancer(trains, recu)` : appelé UNE fois par relevé PRIM reçu. C'est la seule écriture. La cible du relevé précédent s'applique jusqu'à `recu`,
  la nouvelle cible à partir de `recu`.
• `appliquer(ref, train, maintenant)` : lecture seule. L'arrivée adoptée à `maintenant` se calcule en forme fermée à partir de l'état du dernier
  relevé (rampe plafonnée à l'écart restant, gel à l'arrivée). Aucune requête ne modifie l'état.

Le Lisseur est la seule mémoire du serveur sur les relevés passés. Il retient aussi, par course, si PRIM a déjà modifié une heure
depuis la première observation (sert à l'étiquette « Horaire théorique »). Accès protégés par un verrou (serveur multi-fils).
"""
import threading
from dataclasses import replace
from datetime import datetime, timedelta

from position import depart_effectif

TOLERANCE_S = 15.0   # écart entre l'arrivée adoptée et l'heure PRIM en dessous duquel on ne change rien (Mathis, 18/11 : 30 s, puis 15 s après le rejeu : erreur de départ 54 s au lieu de 59 s)
BIAIS_ARRIVEE_S = 10.0   # le train arrive en moyenne plus tard que l'heure estimée par PRIM (relevé de Palaiseau, 18/11 : 26 s, 12 trains) : on ajoute 10 s à chaque arrivée PRIM, valeur de départ décidée par Mathis, à réviser avec un 2e relevé
RETENUE_S = 120.0    # révision PRIM vers plus tard de plus de 120 s (pendant que le train roule) : le train est retenu en pleine voie au lieu de ralentir (= ARRET_ANNONCE_MAX)
RAMPE_MAX = 0.2      # secondes d'arrivée rattrapées par seconde écoulée : la vitesse du train varie de 20 % au plus
VERSION_FORMAT = 1   # format de exporter() / importer()


def _iso(instant):
    return None if instant is None else instant.isoformat()


def _instant(texte):
    return None if texte is None else datetime.fromisoformat(texte)


class Lisseur:
    """Voir la docstring du module. Deux méthodes publiques qui comptent : `avancer` (écrit, une fois par relevé) et `appliquer` (lit).

    # INVARIANT : la sortie de appliquer(ref, train, maintenant) ne dépend que de l'état écrit par avancer, du train et de maintenant ;
    # jamais du nombre, de l'ordre ni de la cadence des appels précédents. Deux suites de requêtes différentes donnent la même arrivée
    # aux mêmes instants (test_lissage.py, TestRejeuRelevesEtRequetes).
    # INVARIANT : appliquer ne modifie jamais l'état (exporter() avant et après est identique).
    """

    def __init__(self, tolerance_s=TOLERANCE_S, rampe=RAMPE_MAX, biais_s=BIAIS_ARRIVEE_S, retenue_s=RETENUE_S):
        self._verrou = threading.Lock()
        self._reglages(tolerance_s, rampe, biais_s, retenue_s)
        self._adoptees = {}   # (course, gare) -> [arrivée adoptée au dernier relevé, instant de ce relevé, arrivée PRIM biaisée visée]
        self._retenues = {}   # (course, gare) -> (arrivée adoptée avant la révision, instant de la révision)
        self._courses = {}    # course -> {"premiere": instant du 1er relevé PRIM, "derniere": instant du dernier, "modifie": bool, "vues": {...}}

    def _reglages(self, tolerance_s, rampe, biais_s, retenue_s):
        self._tolerance_s, self._tolerance = tolerance_s, timedelta(seconds=tolerance_s)
        self._rampe = rampe
        self._biais = timedelta(seconds=biais_s)
        self._retenue = timedelta(seconds=retenue_s)

    # --- écriture : une fois par relevé ---

    def avancer(self, trains, recu):
        """Ingère un relevé PRIM reçu à `recu` : `trains` = {course: Train consolidé}. Appelé une fois par relevé (par la copie qui le reçoit).
        Purge les courses absentes, met à jour les repères (vues, modifié, suivi), avance chaque (course, gare) jusqu'à `recu` avec l'ancienne
        cible, puis adopte la nouvelle cible à partir de `recu`. Une révision de plus de RETENUE_S vers plus tard est datée à `recu`."""
        with self._verrou:
            self._purger(trains.keys())
            for ref, train in trains.items():
                course = self._courses.setdefault(ref, {"premiere": recu, "derniere": recu, "modifie": False, "vues": {}})
                course["derniere"] = max(course["derniere"], recu)
                for p in train.passages:
                    for genre, valeur in (("a", p.arrivee), ("d", p.depart)):
                        if valeur is None:
                            continue
                        premiere = course["vues"].setdefault((p.gare, genre), valeur)
                        if premiere != valeur:
                            course["modifie"] = True
                for p in train.passages:
                    if p.arrivee is not None:
                        self._cibler(ref, p.gare, p.arrivee + self._biais, recu)

    def _cibler(self, ref, gare, cible, recu):
        cle = (ref, gare)
        etat = self._adoptees.get(cle)
        if etat is None:
            self._adoptees[cle] = [cible, recu, cible]   # première observation : valeur PRIM biaisée
            return
        adoptee = self._suivre(cle, etat, recu)           # où en est l'arrivée adoptée à `recu`, avec l'ancienne cible
        if adoptee > recu:                                 # le train n'est pas encore arrivé : la nouvelle cible s'applique
            retenue = self._retenues.get(cle)
            ecart = cible - adoptee
            if retenue:   # déjà retenu : le train est à l'arrêt, il suit PRIM (au-delà de la tolérance), sans jamais arriver avant l'ancienne heure
                if abs(ecart) > self._tolerance:
                    adoptee = max(cible, retenue[0])
            elif ecart > self._retenue:
                self._retenues[cle] = (adoptee, recu)
                adoptee = cible
        self._adoptees[cle] = [adoptee, recu, cible]

    def _purger(self, courses_presentes):
        presentes = set(courses_presentes)
        for cle in [k for k in self._adoptees if k[0] not in presentes]:
            del self._adoptees[cle]
        for cle in [k for k in self._retenues if k[0] not in presentes]:
            del self._retenues[cle]
        for ref in [r for r in self._courses if r not in presentes]:
            del self._courses[ref]

    def purger(self, courses_presentes):
        """Oublie les courses qui ne sont plus dans le relevé (elles repartiraient de la valeur PRIM si elles revenaient). Appelé par avancer."""
        with self._verrou:
            self._purger(courses_presentes)

    # --- lecture : à chaque requête ---

    def appliquer(self, ref, train, maintenant):
        """Train dont les heures sont lissées à l'instant `maintenant` (le même objet s'il n'y a rien à changer). Lecture seule.
        Course sans état (avancer n'a pas encore vu la gare) : la valeur PRIM biaisée, comme une première observation."""
        with self._verrou:
            nouveaux = []
            for p in train.passages:
                if p.arrivee is None:
                    nouveaux.append(p)
                    continue
                cle = (ref, p.gare)
                etat = self._adoptees.get(cle)
                adoptee = p.arrivee + self._biais if etat is None else self._suivre(cle, etat, maintenant)
                decalage = adoptee - p.arrivee   # la tolérance s'applique autour de l'heure biaisée
                if decalage:
                    p = replace(p, arrivee=p.arrivee + decalage, depart=None if p.depart is None else p.depart + decalage)
                retenue = self._retenues.get(cle)
                if retenue and p.arrivee > maintenant:
                    p = replace(p, retenu_depuis=retenue[0], retenu_le=retenue[1])
                nouveaux.append(p)
            if any(a is not b for a, b in zip(train.passages, nouveaux)):
                nouveaux = self._garantir_coherence(train.passages, nouveaux)
        if all(a is b for a, b in zip(train.passages, nouveaux)):
            return train
        return replace(train, passages=nouveaux)

    def _suivre(self, cle, etat, instant):
        """Arrivée adoptée à `instant` (>= instant du relevé de l'état), en forme fermée. Ne modifie rien.
        Rampe : au plus RAMPE_MAX s par seconde, plafonnée à la zone de tolérance ; une fois l'arrivée atteinte (adoptée <= instant), figée."""
        adoptee, releve, cible = etat
        if adoptee <= releve or cle in self._retenues:   # arrivé au relevé, ou retenu en pleine voie : rien ne bouge
            return adoptee
        ecart = cible - adoptee
        if abs(ecart) <= self._tolerance:
            return adoptee
        u = max(0.0, (instant - releve).total_seconds())
        l0 = (adoptee - releve).total_seconds()      # avance de l'arrivée adoptée sur le relevé, en secondes
        lp = (cible - releve).total_seconds()        # avance de l'heure PRIM biaisée sur le relevé
        rampe = self._rampe
        if ecart > self._tolerance:
            # instant où l'adoptée rattrape l'instant (le train arrive) ; la zone de tolérance borne la rampe
            arrivee_s = l0 / (1 - rampe) if rampe < 1 else float("inf")
            plafond = min(arrivee_s, lp - self._tolerance_s)
            avance = min(l0 + rampe * u, plafond)
        else:
            plafond = max(l0 / (1 + rampe), lp + self._tolerance_s)
            avance = max(l0 - rampe * u, plafond)
        return releve + timedelta(seconds=avance)

    def adoptee(self, ref, gare):
        """Arrivée adoptée au dernier relevé pour (course, gare), ou None (pour les tests et l'audit)."""
        with self._verrou:
            etat = self._adoptees.get((ref, gare))
            return etat[0] if etat else None

    def modifie(self, ref):
        """PRIM a-t-il déjà changé une heure de cette course depuis la première observation ? (False si course inconnue)"""
        with self._verrou:
            c = self._courses.get(ref)
            return bool(c and c["modifie"])

    def suivi_s(self, ref):
        """Durée de suivi d'une course, comptée en RELEVÉS PRIM (instant de réception du premier au dernier relevé qui la contient) et non en
        requêtes : si PRIM tombe, le même relevé est servi indéfiniment et la durée ne grandit pas. 0 si course inconnue."""
        with self._verrou:
            c = self._courses.get(ref)
            return (c["derniere"] - c["premiere"]).total_seconds() if c else 0.0

    # --- sauvegarde (T5 : Upstash) ---

    def exporter(self):
        """État en JSON pur (instants en ISO 8601, clés (course, gare) en chaînes, format versionné)."""
        with self._verrou:
            return {
                "v": VERSION_FORMAT,
                "reglages": {"tolerance_s": self._tolerance_s, "rampe": self._rampe,
                             "biais_s": self._biais.total_seconds(), "retenue_s": self._retenue.total_seconds()},
                "adoptees": [[ref, gare, _iso(s), _iso(r), _iso(c)] for (ref, gare), (s, r, c) in self._adoptees.items()],
                "retenues": [[ref, gare, _iso(s), _iso(r)] for (ref, gare), (s, r) in self._retenues.items()],
                "courses": {ref: {"premiere": _iso(c["premiere"]), "derniere": _iso(c["derniere"]), "modifie": c["modifie"],
                                  "vues": [[gare, genre, _iso(v)] for (gare, genre), v in c["vues"].items()]}
                            for ref, c in self._courses.items()},
            }

    def importer(self, d):
        """Remplace l'état (et les réglages) par celui d'un `exporter()`."""
        if d.get("v") != VERSION_FORMAT:
            raise ValueError(f"format de lissage inconnu : {d.get('v')!r}")
        reglages = d["reglages"]
        with self._verrou:
            self._reglages(reglages["tolerance_s"], reglages["rampe"], reglages["biais_s"], reglages["retenue_s"])
            self._adoptees = {(ref, gare): [_instant(s), _instant(r), _instant(c)] for ref, gare, s, r, c in d["adoptees"]}
            self._retenues = {(ref, gare): (_instant(s), _instant(r)) for ref, gare, s, r in d["retenues"]}
            self._courses = {ref: {"premiere": _instant(c["premiere"]), "derniere": _instant(c["derniere"]), "modifie": bool(c["modifie"]),
                                   "vues": {(gare, genre): _instant(v) for gare, genre, v in c["vues"]}}
                             for ref, c in d["courses"].items()}

    # --- garde-fou (fonction pure) ---

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
