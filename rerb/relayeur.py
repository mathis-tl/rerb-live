"""Relayeur Vercel : l'état partagé entre les copies de la fonction (relevé, lissage, mémoire, blocage, dernière tentative)
passe par le magasin Upstash. Une seule copie appelle PRIM à la fois (verrou), au plus une fois par minute (INTERVALLE_S),
jamais la nuit, jamais au-delà du plafond journalier.

Commandes Upstash par requête (l'appel PRIM n'est pas une commande Upstash) :
• relevé frais, cache jusqu'à son échéance (horizon + INTERVALLE_S, ou début de coupure de nuit) : 0 ;
• sinon : 1 lecture sans la mémoire (MGET) ;
• rafraîchissement, après cette lecture : SET NX (verrou), MGET sous verrou (mémoire et compteur du jour), INCR (+ EXPIRE
  au premier appel du jour), MSET de l'état, EVAL (libération) = 6 ; +1 EXPIRE le premier appel du jour ; +1 SET EX quand
  un blocage est écrit. La mémoire n'est réécrite que si elle a changé. Les en-têtes de quota PRIM (`quota_prim`, T6a)
  rejoignent ce même MSET : aucune commande de plus.

# INVARIANT : aucune écriture d'état (relevé, lissage, mémoire) quand l'appel PRIM a échoué ou quand la mémoire dépasse
#             TAILLE_MAX_OCTETS. L'ancien état reste servi.
# INVARIANT : une panne du magasin AVANT l'appel PRIM : aucun appel PRIM ; le dernier relevé de cette copie est servi.
# INVARIANT : une panne d'écriture APRÈS un appel PRIM réussi : le relevé reste servi et n'est pas réappelé pendant
#             INTERVALLE_S par cette copie (non partagé avec les autres copies).
# INVARIANT : le compteur PRIM est incrémenté AVANT l'appel PRIM, et le verrou est libéré dans un `finally`.
"""
import http.client
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from lissage import Lisseur
from magasin import ErreurMagasin, compresser_json, decompresser_json
from memoire import TAILLE_MAX_OCTETS, Memoire
from serveur import (CLE_BLOCAGE, CLE_DERNIERE_TENTATIVE, FENETRE_NUIT, INTERVALLE_S, NOM_VERROU, PARIS, PLAFOND_JOUR, VERROU_S,
                     ErreurPrim, Plafond, Releve, STATUTS_CLE_REFUSEE, consolider_livraison, duree_blocage, est_nuit)

CACHE_LOCAL_S = 20   # délai de cache quand aucune échéance n'est connue (relevé périmé, échec, magasin en panne)
CLE_RELEVE, CLE_LISSEUR, CLE_MEMOIRE, CLE_DERNIERE_ERREUR = "releve", "lisseur", "memoire", "derniere_erreur"
CLE_UTILISES = "plafond_utilises"   # compteur PRIM du jour, recopié à chaque rafraîchissement dans le même MSET : aucune commande de plus
CLE_QUOTA = "quota_prim"            # en-têtes de quota PRIM reçus au dernier appel : {"recu": ISO, "entetes": {...}}
CLES_LECTURE = (CLE_RELEVE, CLE_LISSEUR, CLE_BLOCAGE, CLE_DERNIERE_TENTATIVE, CLE_DERNIERE_ERREUR, CLE_UTILISES)   # sans la mémoire
ERREURS_LECTURE = (KeyError, IndexError, TypeError, ValueError, AttributeError, http.client.HTTPException)


def prochaine_bascule_nuit(maintenant, fenetre):
    """Prochain instant (aware) où `est_nuit` change, ou None sans fenêtre. Calculé en heure de Paris, jamais à partir de l'heure
    système (Vercel tourne en UTC)."""
    if fenetre is None:
        return None
    jour = maintenant.astimezone(PARIS).date()
    candidats = []
    for decalage in (0, 1):
        visé = jour + timedelta(days=decalage)
        for heure in fenetre:
            instant = datetime(visé.year, visé.month, visé.day, heure, tzinfo=PARIS).astimezone(timezone.utc)
            if instant > maintenant:
                candidats.append(instant)
    return min(candidats)


@dataclass(frozen=True)
class Vue:
    """Ce que la requête reçoit : le relevé (ou None), son lissage, l'erreur à signaler, le blocage, et `jusqu` : instant avant
    lequel la vue ne peut pas changer sans que le magasin le dise (fin de blocage, début de coupure, échéance du relevé)."""
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
    compte: int = 0   # compteur PRIM du jour, lu sous verrou seulement


def _copie_lisseur(lisseur):
    copie = Lisseur()
    copie.importer(lisseur.exporter())
    return copie


def _copie_memoire(memoire):
    copie = Memoire()
    copie.importer(memoire.exporter())
    return copie


def _bloc_quota(instant, quota):
    """Valeur de la clé `quota_prim` ; None quand aucune réponse PRIM n'a fourni d'en-têtes (rien n'est écrit, l'ancienne valeur reste).
    # INVARIANT : `quota_prim` rejoint le MSET existant (succès et échec) : 0 commande Upstash de plus."""
    return None if quota is None else {"recu": instant.isoformat(), "entetes": quota}


def _horizon(etat):
    """Dernier instant où le relevé a été reçu ou une tentative faite : c'est lui qui fixe la prochaine actualisation."""
    dates = [d for d in (None if etat.releve is None else etat.releve.recu, etat.derniere) if d is not None]
    return max(dates) if dates else None


class Relayeur:
    """`fetch()` -> livraison PRIM décodée, ou lève ErreurPrim (serveur.py). `magasin` : MagasinUpstash en production."""

    def __init__(self, magasin, fetch, maximum=PLAFOND_JOUR, intervalle_s=INTERVALLE_S, fenetre_nuit=FENETRE_NUIT, quota=None):
        """`quota` (facultatif) : fonction sans argument qui renvoie les en-têtes de quota de la dernière réponse PRIM réussie
        (None si inconnus). Les en-têtes d'une réponse d'erreur viennent de `ErreurPrim.quota`."""
        self._magasin = magasin
        self._fetch = fetch
        self._quota = quota
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
        fin = vue.jusqu if vue.jusqu is not None and vue.jusqu > maintenant else maintenant + timedelta(seconds=CACHE_LOCAL_S)
        self._cache = (vue, fin)
        return vue

    def _repli(self, erreur):
        if self._cache is None:
            return Vue(None, None, Lisseur(), erreur, False)
        precedente = self._cache[0]
        return Vue(None, precedente.releve, precedente.lisseur, erreur, precedente.bloque, utilises=precedente.utilises)

    def _lire_etat(self, maintenant, complet=False):
        """Une commande MGET. `complet` (sous verrou seulement) : lit aussi la mémoire et le compteur PRIM du jour."""
        cle_compte = self._plafond.cle(maintenant)
        cles = CLES_LECTURE + ((CLE_MEMOIRE, cle_compte) if complet else ())
        valeurs = dict(zip(cles, self._magasin.lire_plusieurs(cles)))
        try:
            lisseur, memoire = Lisseur(), Memoire()
            if valeurs[CLE_LISSEUR] is not None:
                lisseur.importer(decompresser_json(valeurs[CLE_LISSEUR]))
            if complet and valeurs[CLE_MEMOIRE] is not None:
                memoire.importer(decompresser_json(valeurs[CLE_MEMOIRE]))
            releve = None if valeurs[CLE_RELEVE] is None else Releve.importer(decompresser_json(valeurs[CLE_RELEVE]))
            derniere = None if valeurs[CLE_DERNIERE_TENTATIVE] is None else datetime.fromisoformat(valeurs[CLE_DERNIERE_TENTATIVE])
            blocage = valeurs[CLE_BLOCAGE]
            blocage_jusqu = None if blocage is None or "jusqu" not in blocage else datetime.fromisoformat(blocage["jusqu"])
            compte = int(valeurs[cle_compte]) if complet and valeurs[cle_compte] is not None else 0
        except ERREURS_LECTURE:
            raise ErreurMagasin("état partagé illisible") from None
        etat = _Etat(releve, lisseur, memoire, blocage, blocage_jusqu, derniere, valeurs[CLE_DERNIERE_ERREUR], valeurs[CLE_UTILISES],
                     compte)
        local = self._local
        if local is not None and (releve is None or local.releve.recu > releve.recu):
            return replace(local, blocage=blocage, blocage_jusqu=blocage_jusqu, compte=compte)   # le relevé local est plus récent
        self._local = None
        return etat

    def _frais(self, etat, maintenant):
        horizon = _horizon(etat)
        return horizon is not None and maintenant - horizon < self._intervalle

    def _prochain_changement(self, horizon, blocage_jusqu, maintenant):
        """Instant (aware) à partir duquel la vue peut changer, ou None (aucune échéance sûre : cache court).
        Bloqué : fin du blocage. Coupure : fin de la coupure. Sinon : horizon + INTERVALLE_S, ou le début de coupure s'il vient avant.
        Relevé périmé : pas d'échéance, pour qu'une copie sans verrou redemande vite."""
        if blocage_jusqu is not None:
            return blocage_jusqu if blocage_jusqu > maintenant else None
        if est_nuit(maintenant, self._fenetre_nuit):
            return prochaine_bascule_nuit(maintenant, self._fenetre_nuit)
        if horizon is None or horizon + self._intervalle <= maintenant:
            return None
        echeance = horizon + self._intervalle
        bascule = prochaine_bascule_nuit(maintenant, self._fenetre_nuit)
        return echeance if bascule is None else min(echeance, bascule)

    def _vue(self, etat, maintenant, erreur=None, service=None):
        bloque = etat.blocage is not None
        if erreur is None:
            erreur = f"PRIM bloqué (statut {etat.blocage.get('statut')})" if bloque else etat.erreur
        jusqu = self._prochain_changement(_horizon(etat), etat.blocage_jusqu if bloque else None, maintenant)
        return Vue(service, etat.releve, etat.lisseur, erreur, bloque, jusqu, etat.utilises)

    def _actualiser(self, maintenant):
        etat = self._lire_etat(maintenant)
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
        etat = self._lire_etat(maintenant, complet=True)   # relecture sous verrou : une autre copie a pu rafraîchir entre-temps
        if etat.blocage is not None:
            return self._vue(etat, maintenant)
        if self._frais(etat, maintenant):
            return self._vue(etat, maintenant)
        if etat.compte >= self._plafond.maximum:
            return self._vue(replace(etat, utilises=etat.compte), maintenant,
                             erreur=f"plafond journalier atteint ({self._plafond.maximum} appels PRIM)")
        # INVARIANT : le compteur est incrémenté AVANT l'appel PRIM : un appel échoué compte quand même.
        etat = replace(etat, utilises=self._plafond.compter(maintenant))
        try:
            livraison = self._fetch()
            memoire = _copie_memoire(etat.memoire)   # la consolidation ne doit pas toucher l'état local si rien n'est écrit
            trains = consolider_livraison(livraison, memoire)
        except ErreurPrim as erreur:
            return self._echec(etat, maintenant, str(erreur), erreur.statut, erreur.quota)
        except ERREURS_LECTURE as erreur:
            return self._echec(etat, maintenant, f"réponse illisible ({type(erreur).__name__})", None, self._lire_quota())
        quota = self._lire_quota()
        memoire.purger(maintenant)
        lisseur = _copie_lisseur(etat.lisseur)
        lisseur.avancer({ref: train for ref, (train, _retards) in trains.items()}, maintenant)
        releve = Releve(maintenant, trains)
        exportee = memoire.exporter()
        taille = memoire.taille_export()
        if taille > TAILLE_MAX_OCTETS:
            erreur = f"mémoire trop grosse ({taille} octets > {TAILLE_MAX_OCTETS}) : état non écrit"
            self._ecrire_echec(maintenant, erreur, etat.utilises, quota)
            return self._vue(etat, maintenant, erreur=erreur)
        ecriture = {
            CLE_RELEVE: compresser_json(releve.exporter()),
            CLE_LISSEUR: compresser_json(lisseur.exporter()),
            CLE_DERNIERE_TENTATIVE: maintenant.isoformat(),
            CLE_DERNIERE_ERREUR: None,
            CLE_UTILISES: etat.utilises,
        }
        if (bloc := _bloc_quota(maintenant, quota)) is not None:
            ecriture[CLE_QUOTA] = bloc
        # La mémoire (la valeur la plus lourde) n'est réécrite que si elle a changé, ou si elle ne vit que dans cette copie.
        if self._local is not None or exportee != etat.memoire.exporter():
            ecriture[CLE_MEMOIRE] = compresser_json(exportee)
        try:
            self._magasin.ecrire_groupe(ecriture)
        except ErreurMagasin:
            self._local = _Etat(releve, lisseur, memoire, None, None, maintenant, None, etat.utilises)
            erreur = "magasin indisponible après l'appel PRIM : relevé gardé localement, non partagé"
            return Vue(None, releve, lisseur, erreur, False, self._prochain_changement(maintenant, None, maintenant), etat.utilises)
        self._local = None
        return Vue(None, releve, lisseur, None, False, self._prochain_changement(maintenant, None, maintenant), etat.utilises)

    def _lire_quota(self):
        return None if self._quota is None else self._quota()

    def _echec(self, etat, maintenant, message, statut, quota=None):
        bloque = statut in STATUTS_CLE_REFUSEE
        blocage_jusqu = None
        if bloque:
            duree = duree_blocage(statut)
            blocage_jusqu = maintenant + timedelta(seconds=duree)
            self._magasin.ecrire(CLE_BLOCAGE, {"statut": statut, "jusqu": blocage_jusqu.isoformat()}, ttl_s=duree)
        self._ecrire_echec(maintenant, message, etat.utilises, quota)
        return Vue(None, etat.releve, etat.lisseur, message, bloque,
                   self._prochain_changement(maintenant, blocage_jusqu, maintenant), etat.utilises)

    def _ecrire_echec(self, maintenant, message, utilises, quota=None):
        valeurs = {CLE_DERNIERE_TENTATIVE: maintenant.isoformat(), CLE_DERNIERE_ERREUR: message, CLE_UTILISES: utilises}
        if (bloc := _bloc_quota(maintenant, quota)) is not None:
            valeurs[CLE_QUOTA] = bloc
        self._magasin.ecrire_groupe(valeurs)
