"""Assemblage de production (Vercel, T5) : configuration par l'environnement, réponses de /trains et /gare, envoi JSON.

Les fonctions `api/trains.py` et `api/gare.py` ne contiennent que le routage HTTP ; la logique est ici, testable sans réseau.
• La clé PRIM vient de `env` (jamais écrite dans une réponse, ni dans un message d'erreur).
• Les noms de variables d'environnement sont acceptés en deux familles : UPSTASH_REDIS_REST_* (nom Upstash) ou KV_REST_API_*
  (nom donné par l'intégration Vercel). Pas de valeur dans les messages de configuration, seulement les noms.
• Sans stockage ou sans clé PRIM : aucun appel PRIM, réponse explicite.
Bibliothèque standard seulement.
"""
import json
from datetime import datetime, timedelta, timezone

from duree_troncon import fournisseur_duree_min
from gare import ReleveurGares
from magasin import ErreurMagasin
from magasin_upstash import MagasinUpstash
from relayeur import Relayeur
from serveur import (CLE_BLOCAGE, CSV_GARES, DELAI_PRIM_S, EN_TETES, FENETRE_NUIT, PLAFOND_JOUR, STATUTS_CLE_REFUSEE, TABLE_DUREES, TRACE,
                     URL_PRIM_ET, URL_PRIM_SM_GARE, ErreurPrim, _charger, appeler_prim, calculer_trains, construire_contexte,
                     corps_trains, duree_blocage, est_nuit, ref_gare)

CACHE_TRAINS = "public, s-maxage=5, stale-while-revalidate=10"   # décision T5 : CDN 5 s, requête locale 20 s (relayeur)
CACHE_GARE = "public, s-maxage=60"                               # une gare : une réponse PRIM au plus par minute et par gare
CACHE_SANS_CACHE = "no-store"
NOMS_URL = ("UPSTASH_REDIS_REST_URL", "KV_REST_API_URL")
NOMS_JETON = ("UPSTASH_REDIS_REST_TOKEN", "KV_REST_API_TOKEN")


def _premier(env, noms):
    for nom in noms:
        valeur = env.get(nom)
        if valeur:
            return valeur
    return None


def ecrire_blocage(magasin, statut, maintenant):
    """Bloque tous les appels PRIM pour `duree_blocage(statut)` secondes, partagé entre les copies. Panne du magasin : ignorée
    (la réponse en erreur est servie quand même ; le blocage local de la copie reste en place)."""
    if magasin is None or statut not in STATUTS_CLE_REFUSEE:
        return
    duree = duree_blocage(statut)
    try:
        magasin.ecrire(CLE_BLOCAGE, {"statut": statut, "jusqu": (maintenant + timedelta(seconds=duree)).isoformat()}, ttl_s=duree)
    except ErreurMagasin:
        pass


class Services:
    """Tout ce qu'il faut pour répondre. Construit une fois par copie de fonction (`api/*.py` le garde en mémoire)."""

    def __init__(self, env, http=None, appeler=None, horloge=None):
        self.horloge = horloge or (lambda: datetime.now(timezone.utc))
        self._appeler = appeler or appeler_prim
        url, jeton = _premier(env, NOMS_URL), _premier(env, NOMS_JETON)
        self.magasin = MagasinUpstash(url, jeton, http=http) if url and jeton else None
        self.cle = env.get("PRIM_API_KEY") or None
        manquant = []
        if self.magasin is None:
            manquant.append("stockage non configuré (UPSTASH_REDIS_REST_URL et UPSTASH_REDIS_REST_TOKEN, ou KV_REST_API_URL et KV_REST_API_TOKEN)")
        if self.cle is None:
            manquant.append("clé PRIM absente (PRIM_API_KEY) : aucun appel PRIM")
        self.erreur_configuration = " ; ".join(manquant) or None
        self.relayeur = Relayeur(self.magasin, self._fetch_trains) if self.magasin is not None else None
        self.releveur_gares = ReleveurGares(self._fetch_gare, None)   # pas de plafond propre : liste blanche + CDN (spec déploiement)
        self.gares = {numero: gare.name for numero, gare in _charger(CSV_GARES, TRACE)[0].items()}
        self.distance, self.localiser, self.nom_gare = construire_contexte(CSV_GARES, TRACE)
        self.duree_min = fournisseur_duree_min(TABLE_DUREES)

    def _fetch_trains(self):
        return self._appeler(self.cle, URL_PRIM_ET, delai=DELAI_PRIM_S)

    def _fetch_gare(self, gare):
        numero = gare.split(":")[3]   # « STIF:StopArea:SP:<numero>: »
        try:
            return self._appeler(self.cle, URL_PRIM_SM_GARE.format(numero), delai=DELAI_PRIM_S)
        except ErreurPrim as erreur:
            ecrire_blocage(self.magasin, erreur.statut, self.horloge())
            raise


def reponse_trains(svc):
    maintenant = svc.horloge()
    if svc.relayeur is None or svc.cle is None:
        return corps_trains(maintenant, False, None, None, svc.erreur_configuration, False, None, [])
    vue = svc.relayeur.lire(maintenant)
    trains = (calculer_trains(vue.releve, maintenant, svc.distance, svc.localiser, svc.nom_gare, svc.duree_min, vue.lisseur)
              if vue.releve is not None else [])
    return corps_trains(maintenant, False, vue.service, vue.releve, vue.erreur, vue.bloque,
                        {"utilises": vue.utilises, "maximum": PLAFOND_JOUR}, trains)


def reponse_gare(svc, ref):
    """`ref` doit être une gare de la liste blanche (`ref_gare`). Ordre : configuration, nuit, blocage écrit, clé, appel."""
    maintenant = svc.horloge()

    def corps(statut, passages=(), age_s=None, erreur=None, bloque=False):
        return {"ref": ref, "gare": svc.gares[ref], "statut": statut, "passages": list(passages), "age_s": age_s,
                "erreur": erreur, "bloque": bloque, "demo": False, "plafond": None}

    if svc.magasin is None:
        return corps("magasin", erreur=svc.erreur_configuration)
    if est_nuit(maintenant, FENETRE_NUIT):
        return corps("nuit")
    try:
        blocage = svc.magasin.lire(CLE_BLOCAGE)
    except ErreurMagasin as erreur:
        return corps("magasin", erreur=f"magasin indisponible ({erreur})")
    if blocage is not None:
        return corps("bloqué", bloque=True, erreur="PRIM bloqué : aucun appel avant l'échéance du blocage")
    if svc.cle is None:
        return corps("erreur", erreur=svc.erreur_configuration)
    resultat = svc.releveur_gares.passages(f"STIF:StopArea:SP:{ref}:", maintenant)
    return corps(resultat["statut"], resultat["passages"], resultat["age_s"], resultat["erreur"], svc.releveur_gares.bloque)


def envoyer(requete, code, corps, cache):
    """Réponse JSON : en-têtes de sécurité communs (EN_TETES), Cache-Control propre à la route, un seul en-tête par nom."""
    octets = json.dumps(corps, ensure_ascii=False).encode("utf-8")
    entetes = {**EN_TETES, "Cache-Control": cache, "Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(octets))}
    requete.send_response(code)
    for nom, valeur in entetes.items():
        requete.send_header(nom, valeur)
    requete.end_headers()
    requete.wfile.write(octets)


__all__ = ["CACHE_GARE", "CACHE_SANS_CACHE", "CACHE_TRAINS", "Services", "ecrire_blocage", "envoyer", "ref_gare", "reponse_gare",
           "reponse_trains"]
