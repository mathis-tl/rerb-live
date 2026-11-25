"""Relayeur Vercel (T5) : l'état partagé entre les copies de la fonction (relevé, lissage, mémoire, blocage, dernière tentative)
passe par le magasin Upstash. Une seule copie appelle PRIM à la fois (verrou), au plus une fois par minute (INTERVALLE_S),
jamais la nuit, jamais au-delà du plafond journalier.

Commandes Upstash par requête :
• relevé frais dans le cache local (≤ CACHE_LOCAL_S, et jamais au-delà de la fin du relevé) : 0 ;
• sinon : 1 lecture (MGET) ;
• rafraîchissement : MGET, verrou (SET NX PX), MGET sous verrou, GET du compteur, INCR (EVAL), MSET de l'état, libération (EVAL)
  = 7 ; 8 quand un blocage est écrit (SET EX). L'appel PRIM n'est pas une commande Upstash.

# INVARIANT : aucune écriture d'état (relevé, lissage, mémoire) quand l'appel PRIM a échoué ou quand la mémoire dépasse
#             TAILLE_MAX_OCTETS. L'ancien état reste servi.
# INVARIANT : une panne du magasin AVANT l'appel PRIM : aucun appel PRIM ; le dernier relevé de cette copie est servi.
# INVARIANT : une panne d'écriture APRÈS un appel PRIM réussi : le relevé reste servi et n'est pas réappelé pendant
#             INTERVALLE_S par cette copie (non partagé avec les autres copies).
"""
import http.client
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from lissage import Lisseur
from magasin import ErreurMagasin, compresser_json, decompresser_json
from memoire import TAILLE_MAX_OCTETS, Memoire
from serveur import (CLE_BLOCAGE, CLE_DERNIERE_TENTATIVE, FENETRE_NUIT, INTERVALLE_S, NOM_VERROU, PLAFOND_JOUR, VERROU_S, ErreurPrim,
                     Plafond, Releve, STATUTS_CLE_REFUSEE, consolider_livraison, duree_blocage, est_nuit)

CACHE_LOCAL_S = 20   # une copie ne relit pas le magasin pendant ce délai : 0 commande pour une requête servie par le cache
CLE_RELEVE, CLE_LISSEUR, CLE_MEMOIRE, CLE_DERNIERE_ERREUR = "releve", "lisseur", "memoire", "derniere_erreur"
CLE_UTILISES = "plafond_utilises"   # compteur PRIM du jour, recopié à chaque rafraîchissement dans le même MSET : aucune commande de plus
CLES = (CLE_RELEVE, CLE_LISSEUR, CLE_MEMOIRE, CLE_BLOCAGE, CLE_DERNIERE_TENTATIVE, CLE_DERNIERE_ERREUR, CLE_UTILISES)
ERREURS_LECTURE = (KeyError, IndexError, TypeError, ValueError, AttributeError, http.client.HTTPException)


@dataclass(frozen=True)
class Vue:
    """Ce que la requête reçoit : le relevé (ou None), son lissage, l'erreur à signaler, le blocage, et `jusqu` : instant avant
    lequel la vue ne peut pas changer sans que le magasin le dise (fin de blocage ou fin de validité du relevé)."""
    service: str | None
    releve: Releve | None
    lisseur: Lisseur
    erreur: str | None
    bloque: bool
    jusqu: datetime | None = None
    utilises: int | None = None   # compteur PRIM du jour au dernier rafraîchissement (None : jamais rafraîchi)


@dataclass(frozen=True)
class _Etat:
    releve: Releve | None
    lisseur: Lisseur
    memoire: Memoire
    blocage: dict | None
    blocage_jusqu: datetime | None
    derniere: datetime | None
    erreur: str | None
    utilises: int | None = None


def _copie_lisseur(lisseur):
    copie = Lisseur()
    copie.importer(lisseur.exporter())
    return copie


def _copie_memoire(memoire):
    copie = Memoire()
    copie.importer(memoire.exporter())
    return copie


class Relayeur:
    """`fetch()` -> livraison PRIM décodée, ou lève ErreurPrim (serveur.py). `magasin` : MagasinUpstash en production."""

    def __init__(self, magasin, fetch, maximum=PLAFOND_JOUR, intervalle_s=INTERVALLE_S, fenetre_nuit=FENETRE_NUIT):
        self._magasin = magasin
        self._fetch = fetch
        self._plafond = Plafond(magasin, maximum)
        self._intervalle = timedelta(seconds=intervalle_s)
        self._fenetre_nuit = fenetre_nuit
        self._cache = None   # (Vue, instant de fin de validité)
        self._local = None   # _Etat : relevé que le magasin n'a pas pu recevoir (panne d'écriture), gardé par cette copie

    def lire(self, maintenant):
        if self._cache is not None and maintenant < self._cache[1]:
            return self._cache[0]
        try:
            vue = self._actualiser(maintenant)
        except ErreurMagasin as erreur:   # aucun appel PRIM : on sert le dernier relevé connu de cette copie
            vue = self._repli(f"magasin indisponible ({erreur})")
        fin = maintenant + timedelta(seconds=CACHE_LOCAL_S)
        if vue.jusqu is not None and vue.jusqu < fin:
            fin = vue.jusqu
        self._cache = (vue, fin)
        return vue

    def _repli(self, erreur):
        if self._cache is None:
            return Vue(None, None, Lisseur(), erreur, False)
        precedente = self._cache[0]
        return Vue(None, precedente.releve, precedente.lisseur, erreur, precedente.bloque, utilises=precedente.utilises)

    def _lire_etat(self):
        releve_t, lisseur_t, memoire_t, blocage, derniere, erreur, utilises = self._magasin.lire_plusieurs(CLES)
        try:
            lisseur, memoire = Lisseur(), Memoire()
            if lisseur_t is not None:
                lisseur.importer(decompresser_json(lisseur_t))
            if memoire_t is not None:
                memoire.importer(decompresser_json(memoire_t))
            releve = None if releve_t is None else Releve.importer(decompresser_json(releve_t))
            derniere = None if derniere is None else datetime.fromisoformat(derniere)
            blocage_jusqu = None if blocage is None or "jusqu" not in blocage else datetime.fromisoformat(blocage["jusqu"])
        except ERREURS_LECTURE:
            raise ErreurMagasin("état partagé illisible") from None
        etat = _Etat(releve, lisseur, memoire, blocage, blocage_jusqu, derniere, erreur, utilises)
        local = self._local
        if local is not None and (releve is None or local.releve.recu > releve.recu):
            return replace(local, blocage=blocage, blocage_jusqu=blocage_jusqu)   # le relevé local est plus récent
        self._local = None
        return etat

    def _frais(self, etat, maintenant):
        if etat.releve is not None and maintenant - etat.releve.recu < self._intervalle:
            return True
        return etat.derniere is not None and maintenant - etat.derniere < self._intervalle

    def _prochain_changement(self, releve, blocage_jusqu, maintenant):
        if blocage_jusqu is not None:
            return blocage_jusqu
        if releve is not None and releve.recu + self._intervalle > maintenant:
            return releve.recu + self._intervalle
        return None

    def _vue(self, etat, maintenant, erreur=None, service=None):
        bloque = etat.blocage is not None
        if erreur is None:
            erreur = f"PRIM bloqué (statut {etat.blocage.get('statut')})" if bloque else etat.erreur
        jusqu = self._prochain_changement(etat.releve, etat.blocage_jusqu if bloque else None, maintenant)
        return Vue(service, etat.releve, etat.lisseur, erreur, bloque, jusqu, etat.utilises)

    def _actualiser(self, maintenant):
        etat = self._lire_etat()
        if etat.blocage is not None:
            return self._vue(etat, maintenant)
        if est_nuit(maintenant, self._fenetre_nuit):
            return self._vue(etat, maintenant, service="nuit")
        if self._frais(etat, maintenant):
            return self._vue(etat, maintenant)
        jeton = self._magasin.prendre_verrou(NOM_VERROU, VERROU_S)
        if jeton is None:   # un autre appel PRIM est en cours : on sert l'état lu
            return self._vue(etat, maintenant)
        try:
            return self._rafraichir(maintenant)
        finally:
            try:
                self._magasin.rendre_verrou(NOM_VERROU, jeton)
            except ErreurMagasin:
                pass   # le verrou expire seul, au plus tard après VERROU_S

    def _rafraichir(self, maintenant):
        etat = self._lire_etat()   # relecture sous verrou : une autre copie a pu rafraîchir entre-temps
        if etat.blocage is not None:
            return self._vue(etat, maintenant)
        if self._frais(etat, maintenant):
            return self._vue(etat, maintenant)
        atteint = self._plafond.compte(maintenant)
        if atteint >= self._plafond.maximum:
            return self._vue(replace(etat, utilises=atteint), maintenant, erreur=f"plafond journalier atteint ({self._plafond.maximum} appels PRIM)")
        # INVARIANT : le compteur est incrémenté AVANT l'appel PRIM : un appel échoué compte quand même.
        etat = replace(etat, utilises=self._plafond.compter(maintenant))
        try:
            livraison = self._fetch()
            memoire = _copie_memoire(etat.memoire)   # la consolidation ne doit pas toucher l'état local si rien n'est écrit
            trains = consolider_livraison(livraison, memoire)
        except ErreurPrim as erreur:
            return self._echec(etat, maintenant, str(erreur), erreur.statut)
        except ERREURS_LECTURE as erreur:
            return self._echec(etat, maintenant, f"réponse illisible ({type(erreur).__name__})", None)
        memoire.purger(maintenant)
        lisseur = _copie_lisseur(etat.lisseur)
        lisseur.avancer({ref: train for ref, (train, _retards) in trains.items()}, maintenant)
        releve = Releve(maintenant, trains)
        taille = memoire.taille_export()
        if taille > TAILLE_MAX_OCTETS:
            erreur = f"mémoire trop grosse ({taille} octets > {TAILLE_MAX_OCTETS}) : état non écrit"
            self._ecrire_echec(maintenant, erreur, etat.utilises)
            return self._vue(etat, maintenant, erreur=erreur)
        try:
            self._magasin.ecrire_groupe({
                CLE_RELEVE: compresser_json(releve.exporter()),
                CLE_LISSEUR: compresser_json(lisseur.exporter()),
                CLE_MEMOIRE: compresser_json(memoire.exporter()),
                CLE_DERNIERE_TENTATIVE: maintenant.isoformat(),
                CLE_DERNIERE_ERREUR: None,
                CLE_UTILISES: etat.utilises,
            })
        except ErreurMagasin:
            self._local = _Etat(releve, lisseur, memoire, None, None, maintenant, None, etat.utilises)
            erreur = "magasin indisponible après l'appel PRIM : relevé gardé localement, non partagé"
            return Vue(None, releve, lisseur, erreur, False, self._prochain_changement(releve, None, maintenant), etat.utilises)
        self._local = None
        return Vue(None, releve, lisseur, None, False, self._prochain_changement(releve, None, maintenant), etat.utilises)

    def _echec(self, etat, maintenant, message, statut):
        bloque = statut in STATUTS_CLE_REFUSEE
        blocage_jusqu = None
        if bloque:
            duree = duree_blocage(statut)
            blocage_jusqu = maintenant + timedelta(seconds=duree)
            self._magasin.ecrire(CLE_BLOCAGE, {"statut": statut, "jusqu": blocage_jusqu.isoformat()}, ttl_s=duree)
        self._ecrire_echec(maintenant, message, etat.utilises)
        return Vue(None, etat.releve, etat.lisseur, message, bloque, self._prochain_changement(etat.releve, blocage_jusqu, maintenant),
                   etat.utilises)

    def _ecrire_echec(self, maintenant, message, utilises):
        self._magasin.ecrire_groupe({CLE_DERNIERE_TENTATIVE: maintenant.isoformat(), CLE_DERNIERE_ERREUR: message, CLE_UTILISES: utilises})
