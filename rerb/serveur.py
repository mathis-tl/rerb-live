"""Tâche 4, J3 : serveur local. Cache du dernier relevé PRIM `estimated-timetable`, position recalculée à chaque requête.

Plan : docs/plan-tache-4.md (J3). Décision S3 (Mathis, 16/11/2026), révisée le 24/11/2026 : au plus 1 appel toutes les 60 s, seulement
quand une requête arrive (carte ouverte), aucun appel de 01:00 à 05:00 (heure de Paris), plafond de 1 300 appels par jour
(quota PRIM : 1 500). Détails : docs/specs/spec-donnees-prim.md.
• consolider_livraison : réponse PRIM -> {course: (Train consolidé par la mémoire M2, retards par gare)}.
• calculer_trains      : relevé + instant -> liste de dicts JSON (état, gare, lat, lon, retard, périmé, âge). Aucun train masqué.
• Plafond              : compteur d'appels du jour (date de Paris), lu et écrit dans le magasin (rerb/magasin.py).
• Releveur             : appelle la source si le cache est trop vieux, hors coupure de nuit, plafond non atteint, clé non refusée ;
                         un seul appel à la fois, toutes copies confondues (verrou du magasin) ; une erreur garde le dernier relevé
                         valide. 401, 403, 429 : plus aucun appel. Magasin en panne : aucun appel.
• creer_serveur        : http.server sur 127.0.0.1 seulement. Routes : /trains, /statut. Le reste : 404.
La clé PRIM est lue dans `.env` (lire_cle), jamais affichée, jamais écrite dans une réponse ni un message d'erreur.
Bibliothèque standard seulement. Les tests n'appellent jamais le réseau (fausse source).
"""
import argparse
import functools
import gzip
import http.client
import json
import math
import sys
import urllib.error
import urllib.parse
import threading
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from duree_troncon import fournisseur_duree_min
from gare import ReleveurGares
from geometry import Geometry, NoValidDistance, _dist, load_morceaux, point_a_distance
from lissage import Lisseur
from magasin import ErreurMagasin, MagasinLocal
from memoire import Memoire, train_depuis_json, train_vers_json
from position import DistanceInconnue, Etat, Reseau, distance_depuis_reseau, estimer, lire_train
from stops import load_stops, stop_area_number
from troncon import cap_degres, chemin_troncon

RACINE = Path(__file__).resolve().parents[1]
CSV_GARES = RACINE / "rerb" / "donnees" / "arrets_rerb_C01743.csv"
TRACE = RACINE / "data" / "static" / "rerb" / "rerb_trace_C01743.json"
WEB = RACINE / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".glb": "model/gltf-binary", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}
COMPRESSIBLES = {".html", ".js", ".css", ".json", ".svg", ".txt"}   # woff2 et png sont déjà compressés
SEUIL_GZIP_OCTETS = 1024


@functools.lru_cache(maxsize=256)
def _gzip_fichier(chemin, mtime_ns, taille):   # le contenu du fichier est relu, mtime et taille ne servent que de clé de cache
    return gzip.compress(Path(chemin).read_bytes(), compresslevel=6, mtime=0)


# Le navigateur lui-même refuse toute requête hors de ce serveur (aucun CDN, aucune tuile, aucun envoi à un tiers).
EN_TETES = {
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; "
                                "font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
INTERVALLE_S = 60
PLAFOND_JOUR = 1300
NUIT_DEBUT_H = 1      # coupure de nuit : aucun appel PRIM de 01:00 (inclus) à 05:00 (exclu), heure de Paris
NUIT_FIN_H = 5
FENETRE_NUIT = (NUIT_DEBUT_H, NUIT_FIN_H)
DELAI_PRIM_S = 20     # délai maximal d'un appel PRIM
# INVARIANT : VERROU_S > DELAI_PRIM_S + 10. Le verrou doit survivre à l'appel le plus long ; sinon un second appel PRIM part pendant
# le premier, toutes copies confondues, et le plafond est faux.
VERROU_S = 35
NOM_VERROU = "prim"
CLE_BLOCAGE = "blocage"
CLE_DERNIERE_TENTATIVE = "derniere_tentative"
PARIS = ZoneInfo("Europe/Paris")
STATUTS_CLE_REFUSEE = (401, 403, 429)
URL_PRIM_ET = "https://prim.iledefrance-mobilites.fr/marketplace/estimated-timetable?LineRef=STIF%3ALine%3A%3AC01743%3A"
URL_PRIM_SM_GARE = "https://prim.iledefrance-mobilites.fr/marketplace/stop-monitoring?MonitoringRef=STIF%3AStopArea%3ASP%3A{}%3A"
PLAFOND_GARES_JOUR = 5000   # quota PRIM stop-monitoring : 1 000 000 par jour


class ErreurPrim(Exception):
    """Échec d'un appel PRIM ; `statut` = code HTTP si connu (jamais d'information secrète dans le message).
    `quota` : en-têtes de quota reçus avec la réponse d'erreur (HTTP), None si aucune réponse n'a été reçue."""

    def __init__(self, message, statut=None, quota=None):
        super().__init__(message)
        self.statut = statut
        self.quota = quota


VERSION_RELEVE = 1
BLOCAGE_429_S = 900       # PRIM limite le débit : 15 min avant un nouvel essai
BLOCAGE_REFUS_S = 21600   # clé refusée (401/403) : 6 h, le temps qu'un humain intervienne (levée : supprimer la clé « blocage »)


def duree_blocage(statut):
    """Durée du blocage (secondes) après un refus PRIM de code `statut` ; 0 si ce code ne bloque pas."""
    if statut == 429:
        return BLOCAGE_429_S
    if statut in (401, 403):
        return BLOCAGE_REFUS_S
    return 0


@dataclass(frozen=True)
class Releve:
    """Instantané IMMUABLE du dernier relevé : le fil qui interroge PRIM le remplace d'un coup (une seule affectation)."""
    recu: datetime
    trains: dict  # course -> (Train consolidé, {gare: retard en secondes})

    def exporter(self):
        """JSON pur (format versionné) : état complet d'un relevé, pour le magasin Upstash."""
        return {"v": VERSION_RELEVE, "recu": self.recu.isoformat(),
                "trains": {ref: {**train_vers_json(train), "retards": retards} for ref, (train, retards) in self.trains.items()}}

    @classmethod
    def importer(cls, d):
        if d.get("v") != VERSION_RELEVE:
            raise ValueError(f"format de relevé inconnu : {d.get('v')!r}")
        return cls(datetime.fromisoformat(d["recu"]),
                   {ref: (train_depuis_json(t), {gare: float(s) for gare, s in t["retards"].items()})
                    for ref, t in d["trains"].items()})


def _valeur(x):
    return x.get("value") if isinstance(x, dict) else x


def _heure(texte):
    return datetime.fromisoformat(texte.replace("Z", "+00:00")) if texte else None


def _retards(ej):
    """{gare: retard en s} = heure estimée moins heure prévue (arrivée sinon départ), absent si l'une manque."""
    calls = ej.get("EstimatedCalls") or []
    if isinstance(calls, dict):
        calls = calls.get("EstimatedCall") or []
    sortie = {}
    for c in calls:
        for prevue, estimee in (("AimedArrivalTime", "ExpectedArrivalTime"), ("AimedDepartureTime", "ExpectedDepartureTime")):
            if c.get(prevue) and c.get(estimee):
                sortie[c["StopPointRef"]["value"]] = (_heure(c[estimee]) - _heure(c[prevue])).total_seconds()
                break
    return sortie


def consolider_livraison(livraison, memoire):
    """Réponse PRIM (ServiceDelivery) -> {course: (Train, retards)}. Course sans DatedVehicleJourneyRef ignorée."""
    trains = {}
    for cadre in livraison["EstimatedTimetableDelivery"][0].get("EstimatedJourneyVersionFrame") or []:
        for ej in cadre.get("EstimatedVehicleJourney") or []:
            ref = _valeur(ej.get("DatedVehicleJourneyRef"))
            if ref is not None:
                trains[ref] = (memoire.consolider(ref, lire_train(ej)), _retards(ej))
    return trains


def _gare_suivante(train, gare):
    """Gare qui suit `gare` dans l'ordre des heures du train, ou None (terminus)."""
    ordre = [p.gare for p in sorted(train.passages, key=lambda p: p.arrivee or p.depart)]
    i = ordre.index(gare)
    return ordre[i + 1] if i + 1 < len(ordre) else None


DEMI_VOIE_M = 170.0   # demi-longueur du tracé de voie envoyé avec chaque train : demi-rame (52 m) + déplacement entre deux relevés (≤ 100 m) + marge
PAS_VOIE_M = 8.0      # un point tous les 8 m : assez fin pour les courbes (rayon ≥ 100 m), corde d'un bogie de 18,5 m
VITESSE_MAX_MS = 33.0   # 119 km/h : vitesse maximale du RER B (120 km/h) ; plancher de durée (distance / 33) et repli si tronçon inconnu
TABLE_DUREES = RACINE / "rerb" / "donnees" / "duree_min_troncon.json"   # T1 : durée minimale plausible par tronçon (correction estimée)


def _nom_sur(nom_gare, ref):
    """Nom lisible d'une gare ; si PRIM envoie une référence absente de notre liste, on garde la référence brute."""
    if not ref:
        return None
    try:
        return nom_gare(ref)
    except (KeyError, ValueError):
        return ref


OBSERVATION_MIN_S = 600.0   # « Horaire théorique » seulement après 10 min de suivi en relevés PRIM distincts (au moins 5 à 60 s) : un train vu une fois, ou un relevé figé, ne prouve rien

INCERTITUDE_MEDIANE_S = 44.0   # J2 bis (16/11/2026) : médiane de |départ réel - départ estimé|, mesurée à quai (docs/recap-j2bis-verite-gares.md)
INCERTITUDE_P90_S = 117.0      # J2 bis : 90e centile. Ordres de grandeur d'un seul jour, pas une erreur mesurée en pleine voie ; biais +53 s non appliqué
DECALAGES_S = tuple(sorted(set(range(-112, 113, 8)) | {-117, -44, 44, 117}))   # instants échantillonnés autour de `maintenant` (au plus ~8 s, soit ~120 m à 14,7 m/s : la droite entre deux points suit la voie à cette échelle) ; la médiane garde |décalage| <= 44 s


def _distance_corrigee(e, duree_min):
    """Distance depuis `e.gare` après correction d'un tronçon annoncé trop court (règle commune à la position et à l'incertitude).
    -> (distance_m, vitesse implicite, minimum_s, source_min, incoherent, corrige)."""
    distance_m, vitesse, incoherent, corrige, minimum_s, source_min = e.distance_m, None, False, False, None, None
    if e.etat == Etat.EN_ROUTE and e.duree_s and e.longueur_m is not None:
        vitesse = e.longueur_m / e.duree_s
        plancher_s = e.longueur_m / VITESSE_MAX_MS
        minimum_s, source_min = plancher_s, "plancher"
        estimee = duree_min(e.gare, e.gare_suivante) if duree_min else None
        if estimee and estimee[0] > plancher_s:
            minimum_s, source_min = estimee[0], estimee[1]
        if e.duree_s < minimum_s:
            # Durée annoncée impossible (ex. 1 435 m en 60 s) : le train est supposé mettre `minimum_s` (durée minimale
            # plausible du tronçon, sinon plancher 33 m/s) ; il n'avance jamais plus vite que cela depuis son départ.
            incoherent = True
            corrige = source_min == "estimée"
            distance_m = min(e.distance_m, e.longueur_m * (e.fraction * e.duree_s) / minimum_s)
    return distance_m, vitesse, minimum_s, source_min, incoherent, corrige


def _incertitude(train, maintenant, distance, localiser, duree_min):
    """Chemin parcouru par le train entre t - delta et t + delta (le long des rails, mêmes règles que la position affichée).
    Un instant où le train n'est pas placé (avant la 1re gare, après le terminus, géométrie impossible) est omis, jamais inventé."""
    points = []
    for d in DECALAGES_S:
        e = estimer(train, maintenant + timedelta(seconds=d), distance)
        if e.etat not in (Etat.A_QUAI, Etat.EN_ROUTE):
            continue
        distance_m = _distance_corrigee(e, duree_min)[0]
        suivante = e.gare_suivante if e.etat == Etat.EN_ROUTE else _gare_suivante(train, e.gare)
        try:
            lat, lon, _cap = localiser(e.gare, suivante, distance_m)
        except (KeyError, ValueError, DistanceInconnue):
            continue
        if points and points[-1][1] == [lat, lon]:
            continue   # train à l'arrêt : un point répété ne fait pas un trait (longueur nulle)
        points.append((d, [lat, lon]))
    return {"mediane_s": INCERTITUDE_MEDIANE_S, "p90_s": INCERTITUDE_P90_S,
            "mediane": [pt for d, pt in points if abs(d) <= INCERTITUDE_MEDIANE_S],
            "p90": [pt for _d, pt in points]}


def calculer_trains(releve, maintenant, distance, localiser, nom_gare, duree_min=None, lisseur=None):
    """Un dict par train du relevé, à l'instant `maintenant`. `localiser(gare, gare_suivante, distance_m)` ->
    (lat, lon, cap en degrés ou None) ; il peut lever une erreur de géométrie : le train reste dans la liste,
    sans position, avec son motif. À quai, `gare_suivante` donne l'orientation de la rame (None au terminus).
    `duree_min(ref_gare, ref_gare_suivante)` -> (secondes, source) ou None (tronçon inconnu) : durée minimale plausible du
    tronçon (T1, correction estimée). Jamais sous le plancher distance / VITESSE_MAX_MS, qui sert aussi de repli.
    `lisseur` (rerb/lissage.py, facultatif) : les heures PRIM sont lissées avant l'estimation de position (une révision de PRIM ne fait
    pas sauter le train). Lecture seule : le relevé a été avancé dans le lisseur une fois, à sa réception (charge_utile)."""
    sortie = []
    for ref, (train, retards) in releve.trains.items():
        if lisseur is not None:
            train = lisseur.appliquer(ref, train, maintenant)
        e = estimer(train, maintenant, distance)
        lat = lon = cap = None
        motif = e.motif
        distance_m, vitesse, minimum_s, source_min, incoherent, corrige = _distance_corrigee(e, duree_min)
        motif_vitesse = None
        if incoherent:
            if corrige:
                motif_vitesse = (f"horaires incohérents : {e.longueur_m:.0f} m en {e.duree_s:.0f} s ({vitesse * 3.6:.0f} km/h) ; "
                                 f"correction estimée : durée minimale plausible de ce tronçon {minimum_s:.0f} s, mesurée sur des relevés passés")
            else:
                motif_vitesse = (f"horaires incohérents : {e.longueur_m:.0f} m en {e.duree_s:.0f} s "
                                 f"({vitesse * 3.6:.0f} km/h) ; vitesse affichée limitée à {VITESSE_MAX_MS * 3.6:.0f} km/h")
        terminus = False
        if e.etat in (Etat.A_QUAI, Etat.EN_ROUTE):
            suivante = e.gare_suivante if e.etat == Etat.EN_ROUTE else _gare_suivante(train, e.gare)
            terminus = e.etat == Etat.A_QUAI and suivante is None   # arrivé au terminus : reste à quai 60 s puis disparaît (affiché en rouge)
            try:
                lat, lon, cap = localiser(e.gare, suivante, distance_m)
            except (KeyError, ValueError, DistanceInconnue) as erreur:
                motif = f"position impossible ({erreur})"
        trace_voie = None
        fonction_voie = getattr(localiser, "voie", None)
        if lat is not None and fonction_voie is not None:
            try:
                trace_voie = fonction_voie(e.gare, suivante, distance_m)
            except (KeyError, ValueError, DistanceInconnue):
                trace_voie = None   # la carte posera la rame en ligne droite
        gare_pertinente = e.gare_suivante or e.gare
        retard = retards.get(gare_pertinente) if gare_pertinente else None
        # « Horaire théorique » (indicatif) : l'heure estimée égale l'horaire ET PRIM ne l'a jamais modifiée depuis plus de 10 min de suivi. Un train
        # à l'heure donne aussi des heures égales : on ne parle donc jamais de « sans temps réel », seulement d'un horaire sans écart annoncé.
        horaire_theorique = (lisseur is not None and retard == 0 and not lisseur.modifie(ref)
                             and lisseur.suivi_s(ref) >= OBSERVATION_MIN_S)
        sortie.append({
            "course": ref, "etat": e.etat.value, "motif": motif,
            "horaires_incoherents": incoherent, "vitesse_implicite_ms": vitesse, "motif_vitesse": motif_vitesse,
            "correction_estimee": corrige, "duree_min_s": minimum_s, "source_duree_min": source_min,
            "gare": _nom_sur(nom_gare, e.gare),
            "gare_suivante": _nom_sur(nom_gare, e.gare_suivante),
            "lat": lat, "lon": lon, "cap": cap, "voie": trace_voie, "terminus": terminus,
            "retard_s": retard,
            "donnees_anciennes": e.perime,   # relevé de la course vieux de plus de 15 min (SEUIL_PERIME) ; remplace « périmé », mot trompeur
            "perime": e.perime,              # ancien nom, conservé un temps pour la compatibilité (la page ne le lit plus)
            "horaire_theorique": horaire_theorique,
            "retenu": e.retenu,              # en route mais arrêté en pleine voie : PRIM a repoussé l'arrivée de plus de 120 s (décision du 21/11)
            "reconstruit": e.reconstruit, "repare": e.repare,
            "incertitude": _incertitude(train, maintenant, distance, localiser, duree_min) if lat is not None else None,
            "age_s": None if train.recorded_at is None else int((maintenant - train.recorded_at).total_seconds()),
        })
    return sortie


def jour_paris(instant):
    """Date civile de Paris de `instant` (aware). Jamais l'heure système : Vercel tourne en UTC."""
    return instant.astimezone(PARIS).date()


def est_nuit(maintenant, fenetre):
    """True si `maintenant` tombe dans la coupure `fenetre` = (heure de début, heure de fin), heure de Paris ; `fenetre` None : jamais.
    # INVARIANT : l'heure se calcule en Europe/Paris, jamais à partir de l'heure système (Vercel tourne en UTC ; l'heure d'été décale)."""
    if fenetre is None:
        return False
    debut, fin = fenetre
    return debut <= maintenant.astimezone(PARIS).hour < fin


class Plafond:
    """Nombre d'appels PRIM du jour, lu et écrit dans `magasin` sous la clé `<nom>:<AAAA-MM-JJ>` (date de Paris).
    `instant` absent : l'heure vient de `horloge` (UTC réel par défaut). Une panne du magasin lève ErreurMagasin."""

    def __init__(self, magasin, maximum, horloge=None, nom="plafond"):
        self.magasin, self.maximum, self._nom = magasin, maximum, nom
        self._horloge = horloge or (lambda: datetime.now(timezone.utc))

    def cle(self, instant=None):
        return f"{self._nom}:{jour_paris(self._horloge() if instant is None else instant).isoformat()}"

    def compte(self, instant=None):
        return self.magasin.lire_compteur(self.cle(instant))

    @property
    def utilises(self):
        return self.compte()

    def autorise(self, instant=None):
        return self.compte(instant) < self.maximum

    def compter(self, instant=None):
        return self.magasin.incrementer_compteur(self.cle(instant))


def _utilises(plafond):
    """Compteur pour l'affichage : None si le magasin est en panne (la réponse reste servie)."""
    try:
        return plafond.utilises
    except ErreurMagasin:
        return None


def corps_trains(maintenant, demo, service, releve, erreur, bloque, plafond, trains):
    """Réponse JSON de /trains, commune au serveur local et à la fonction Vercel (mêmes clés). `releve` : Releve ou None."""
    return {
        "maintenant": maintenant.isoformat(), "demo": demo, "service": service,   # service : « nuit » pendant la coupure, sinon None
        "releve_recu": releve.recu.isoformat() if releve is not None else None,
        "releve_age_s": int((maintenant - releve.recu).total_seconds()) if releve is not None else None,
        "erreur": erreur, "bloque": bloque, "plafond": plafond, "trains": trains,
    }


def ref_gare(chemin, gares):
    """Valeur du paramètre `ref` si c'est EXACTEMENT un paramètre de la liste blanche `gares` (et rien d'autre dans la requête), sinon None."""
    requete = urllib.parse.parse_qs(urllib.parse.urlsplit(chemin).query, keep_blank_values=True, strict_parsing=False)
    valeurs = requete.get("ref", [])
    if len(requete) != 1 or len(valeurs) != 1 or valeurs[0] not in gares:
        return None
    return valeurs[0]


class Releveur:
    """Cache du dernier relevé. `fetch()` -> livraison PRIM décodée, ou lève ErreurPrim.
    L'état partagé (verrou, dernière tentative, blocage, plafond) est dans le magasin du plafond : deux copies du serveur qui
    partagent ce magasin ne font jamais deux appels PRIM à la fois ni au-delà du plafond."""

    def __init__(self, fetch, plafond, intervalle_s=INTERVALLE_S, fenetre_nuit=FENETRE_NUIT):
        self._fetch, self._plafond, self._magasin = fetch, plafond, plafond.magasin
        self._intervalle = timedelta(seconds=intervalle_s)
        self._fenetre_nuit = fenetre_nuit
        self._memoire = Memoire()
        self.releve = None
        self.derniere_erreur = None
        self.bloque = False
        self._bloque_jusqu = None    # fin du blocage 401/403/429 gardé en local (même durée que dans le magasin, qui l'expire seul)

    @property
    def plafond(self):
        return self._plafond

    def est_nuit(self, maintenant):
        """True pendant la coupure. `fenetre_nuit` None : jamais de coupure (démo)."""
        return est_nuit(maintenant, self._fenetre_nuit)

    def actualiser(self, maintenant):
        """-> 'ok', 'trop tôt', 'plafond', 'bloqué', 'nuit', 'occupé', 'erreur' ou 'magasin'. Au plus un appel PRIM en cours.
        Ordre des contrôles : bloqué, nuit, trop tôt, plafond, verrou, appel. Une panne du magasin : 'magasin', aucun appel."""
        magasin = self._magasin
        try:
            locale = self._bloque_jusqu is not None and maintenant < self._bloque_jusqu
            self.bloque = locale or magasin.lire(CLE_BLOCAGE) is not None
            if self.bloque:
                return "bloqué"
            # Nuit : aucun appel, aucun comptage. Le relevé reste en cache (son âge continue de croître).
            if self.est_nuit(maintenant):
                return "nuit"
            jeton = magasin.prendre_verrou(NOM_VERROU, VERROU_S)
            if jeton is None:
                return "occupé"
            try:
                return self._appeler(maintenant)
            finally:
                try:
                    magasin.rendre_verrou(NOM_VERROU, jeton)
                except ErreurMagasin:
                    pass   # le verrou expire seul, au plus tard après VERROU_S
        except ErreurMagasin as erreur:
            self.derniere_erreur = f"magasin indisponible ({erreur})"
            return "magasin"

    def _appeler(self, maintenant):
        """Contrôles sous verrou (trop tôt, plafond), puis l'appel PRIM. Lève ErreurMagasin si le magasin tombe avant l'appel."""
        magasin = self._magasin
        derniere = magasin.lire(CLE_DERNIERE_TENTATIVE)
        if derniere is not None and maintenant - datetime.fromisoformat(derniere) < self._intervalle:
            return "trop tôt"
        if not self._plafond.autorise(maintenant):
            return "plafond"
        # INVARIANT : le compteur et la dernière tentative sont écrits AVANT l'appel PRIM. Un appel échoué ou expiré compte quand même,
        # pour ne jamais sous-estimer la consommation du quota.
        self._plafond.compter(maintenant)
        magasin.ecrire(CLE_DERNIERE_TENTATIVE, maintenant.isoformat())
        try:
            livraison = self._fetch()
            trains = consolider_livraison(livraison, self._memoire)
        except ErreurPrim as erreur:
            self.derniere_erreur = str(erreur)
            if erreur.statut in STATUTS_CLE_REFUSEE:
                duree = duree_blocage(erreur.statut)
                self.bloque = True
                self._bloque_jusqu = maintenant + timedelta(seconds=duree)
                try:
                    magasin.ecrire(CLE_BLOCAGE, {"statut": erreur.statut}, ttl_s=duree)
                except ErreurMagasin:
                    pass   # blocage gardé en local jusqu'à son échéance
            return "erreur"
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, http.client.HTTPException) as erreur:
            self.derniere_erreur = f"réponse illisible ({type(erreur).__name__})"
            return "erreur"
        self.releve = Releve(maintenant, trains)   # une seule affectation : les requêtes lisent une copie cohérente
        self.derniere_erreur = None
        return "ok"


def lire_cle(chemin_env, nom="PRIM_API_KEY"):
    """Valeur de `nom` dans un fichier .env. Les messages d'erreur ne contiennent jamais de valeur."""
    try:
        lignes = Path(chemin_env).read_text(encoding="utf-8").splitlines()
    except OSError:
        raise ValueError(f"fichier .env illisible : {chemin_env}") from None
    for ligne in lignes:
        cle, _, valeur = ligne.partition("=")
        if cle.strip() == nom and valeur.strip():
            return valeur.strip().strip('"').strip("'")
    raise ValueError(f"{nom} absente de {chemin_env}")


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None          # la redirection devient une erreur HTTP 3xx, signalée telle quelle


_OUVREUR_SANS_REDIRECTION = urllib.request.build_opener(_SansRedirection)
TAILLE_MAX_REPONSE = 60_000_000      # octets ; l'estimated-timetable du RER B fait ~1 Mo


QUOTA_LONGUEUR_MAX = 64   # une valeur d'en-tête de quota est un nombre ou une date : au-delà, c'est anormal et on la coupe


def quota_depuis(entetes):
    """Liste blanche des en-têtes de quota PRIM : `x-ratelimit*` et `retry-after`, noms en minuscules, valeurs tronquées.
    Tout autre en-tête (et la clé `apikey`) est ignoré.
    # INVARIANT : aucune valeur d'en-tête hors liste blanche n'est stockée ni exposée ; `apikey` n'apparaît jamais ici."""
    sortie = {}
    for nom, valeur in (entetes or {}).items():
        minuscules = nom.lower()
        if minuscules.startswith("x-ratelimit") or minuscules == "retry-after":
            sortie[minuscules] = str(valeur)[:QUOTA_LONGUEUR_MAX]
    return sortie


def appeler_prim_avec_quota(cle, url=URL_PRIM_ET, delai=DELAI_PRIM_S):
    """Un appel GET PRIM -> (ServiceDelivery décodé, quota). `quota` : voir `quota_depuis`. Le message d'erreur ne contient
    ni la clé ni les en-têtes."""
    requete = urllib.request.Request(url, headers={"apikey": cle, "Accept": "application/json"})
    try:
        # Aucune redirection suivie : l'en-tête `apikey` ne doit jamais partir vers une autre adresse.
        with _OUVREUR_SANS_REDIRECTION.open(requete, timeout=delai) as r:
            quota = quota_depuis(r.headers)
            corps = r.read(TAILLE_MAX_REPONSE + 1)
            if len(corps) > TAILLE_MAX_REPONSE:
                raise ErreurPrim("réponse trop grande")
            return json.loads(corps)["Siri"]["ServiceDelivery"], quota
    except urllib.error.HTTPError as e:
        raise ErreurPrim(f"HTTP {e.code}", e.code, quota=quota_depuis(e.headers)) from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
        raise ErreurPrim(f"réseau ({type(e).__name__})") from None
    except (ValueError, KeyError, TypeError) as e:
        raise ErreurPrim(f"réponse illisible ({type(e).__name__})") from None


def appeler_prim(cle, url=URL_PRIM_ET, delai=DELAI_PRIM_S):
    """Un appel GET PRIM -> JSON décodé (sans le quota). Le message d'erreur ne contient ni la clé ni les en-têtes."""
    return appeler_prim_avec_quota(cle, url, delai)[0]


def fichier_statique(racine, chemin_url):
    """Chemin d'URL -> fichier de `racine` à servir, ou None. Refuse : sortie de la racine (.., liens), fichiers cachés,
    dossiers, octets nuls, types hors liste TYPES."""
    brut = urllib.parse.unquote(chemin_url.split("?")[0].split("#")[0])
    if "\x00" in brut:
        return None
    if brut == "/":
        brut = "/index.html"
    parties = brut.lstrip("/").split("/")
    if any(p == "" or p.startswith(".") for p in parties):
        return None
    racine = Path(racine).resolve()
    f = (racine / "/".join(parties)).resolve()
    if not f.is_relative_to(racine) or not f.is_file() or f.suffix.lower() not in TYPES:
        return None
    return f


def creer_serveur(releveur, distance, localiser, nom_gare, port=8765, horloge=None, racine_web=None, geo=None,
                  demo=False, releveur_gares=None, gares=None, duree_min=None, lisseur=None):
    """Serveur HTTP sur 127.0.0.1 seulement. `horloge()` -> instant UTC (remplaçable en test).
    Routes : /trains, /statut, /geo (tracé et gares), fichiers de `racine_web` (défaut : web/). Le reste : 404.
    /gare?ref=<numéro> : prochains trains d'une gare (stop-monitoring), seulement si `releveur_gares` et `gares`
    ({numéro: nom}, la liste blanche des gares autorisées) sont fournis ; toute autre valeur : 404, aucun appel PRIM.
    demo=True : les trains sont INVENTÉS ; la réponse porte `"demo": true` pour que la page l'annonce."""
    horloge = horloge or (lambda: datetime.now(timezone.utc))
    lisseur = lisseur if lisseur is not None else Lisseur()   # un lisseur par serveur : la seule mémoire des relevés passés
    racine_web = Path(racine_web) if racine_web is not None else WEB
    relevé_vu = {"releve": None}   # dernier relevé donné au lisseur
    verrou_relevé = threading.Lock()

    def charge_utile():
        maintenant = horloge()
        releveur.actualiser(maintenant)
        r = releveur.releve   # copie de la référence : le relevé est immuable
        if r is not None:
            with verrou_relevé:
                # INVARIANT : chaque relevé n'est avancé dans le lisseur qu'UNE fois, à sa réception (identité de l'objet), quelle que soit la
                # cadence des requêtes : c'est ce qui rend le lissage rejouable (rerb/lissage.py, Lisseur.avancer).
                if r is not relevé_vu["releve"]:
                    lisseur.avancer({ref: train for ref, (train, _retards) in r.trains.items()}, r.recu)
                    relevé_vu["releve"] = r
        return corps_trains(
            maintenant, demo, "nuit" if releveur.est_nuit(maintenant) else None, r,   # coupure de nuit : pas d'appel PRIM, relevé gardé
            releveur.derniere_erreur, releveur.bloque,
            {"utilises": _utilises(releveur.plafond), "maximum": releveur.plafond.maximum},
            calculer_trains(r, maintenant, distance, localiser, nom_gare, duree_min, lisseur) if r else [])

    class Gestionnaire(BaseHTTPRequestHandler):
        cache_controle = None   # remplace "no-store" pour les fichiers statiques (revalidation par ETag)

        def end_headers(self):   # en-têtes de sécurité sur TOUTES les réponses, erreurs comprises
            for cle, valeur in EN_TETES.items():
                self.send_header(cle, self.cache_controle if cle == "Cache-Control" and self.cache_controle else valeur)
            super().end_headers()

        def _envoyer(self, octets, type_mime):
            self.send_response(200)
            self.send_header("Content-Type", type_mime)
            self.send_header("Content-Length", str(len(octets)))
            self.end_headers()
            self.wfile.write(octets)

        def _envoyer_statique(self, f, corps=True):
            """Fichier du site : ETag (taille + date) pour que le navigateur revalide sans retélécharger (304), gzip pour les gros fichiers texte."""
            st = f.stat()
            gzip_ok = (f.suffix.lower() in COMPRESSIBLES and st.st_size >= SEUIL_GZIP_OCTETS
                       and "gzip" in self.headers.get("Accept-Encoding", "").lower())
            etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}{"-gz" if gzip_ok else ""}"'
            self.cache_controle = "no-cache"
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.end_headers()
                return
            octets = _gzip_fichier(str(f), st.st_mtime_ns, st.st_size) if gzip_ok else f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", TYPES[f.suffix.lower()])
            self.send_header("Content-Length", str(len(octets)))
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept-Encoding")
            if gzip_ok:
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            if corps:
                self.wfile.write(octets)

        def do_HEAD(self):
            f = fichier_statique(racine_web, self.path) if self.path.split("?")[0] not in ("/trains", "/statut", "/gare", "/geo") else None
            if f is None:
                self.send_error(404)
                return
            self._envoyer_statique(f, corps=False)

        def do_GET(self):
            route = self.path.split("?")[0]
            if route in ("/trains", "/statut"):
                corps = charge_utile()
                if route == "/statut":
                    corps.pop("trains")
                self._envoyer(json.dumps(corps, ensure_ascii=False).encode("utf-8"), TYPES[".json"])
            elif route == "/gare" and releveur_gares is not None and gares is not None:
                ref = ref_gare(self.path, gares)
                if ref is None:
                    self.send_error(404)
                    return
                resultat = releveur_gares.passages(f"STIF:StopArea:SP:{ref}:", horloge())
                plafond = releveur_gares.plafond   # None en production (Vercel : pas de plafond propre, voir gare.py)
                corps = {"ref": ref, "gare": gares[ref], **resultat, "demo": demo, "bloque": releveur_gares.bloque,
                         "plafond": None if plafond is None else {"utilises": _utilises(plafond), "maximum": plafond.maximum}}
                self._envoyer(json.dumps(corps, ensure_ascii=False).encode("utf-8"), TYPES[".json"])
            elif route == "/geo" and geo is not None:
                self._envoyer(json.dumps(geo, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), TYPES[".json"])
            else:
                f = fichier_statique(racine_web, self.path)
                if f is None:
                    self.send_error(404)
                    return
                self._envoyer_statique(f)

        def log_message(self, *args):  # pas de journal d'accès sur l'écran
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Gestionnaire)


@functools.lru_cache(maxsize=2)
def _charger(csv_gares, trace):
    gares = load_stops(csv_gares)
    geometrie = Geometry(load_morceaux(trace), {n: (g.lat, g.lon) for n, g in gares.items()})
    reseau = Reseau({p: geometrie.distance(*p) for p in geometrie.neighbor_pairs()})
    return gares, geometrie, reseau


def construire_contexte(csv_gares=CSV_GARES, trace=TRACE):
    """Fichiers statiques -> (distance, localiser, nom_gare) pour `calculer_trains`. Hors ligne, aucun appel API.
    localiser(gare, gare_suivante, distance_m) -> (lat, lon, cap) : sur les rails à moins de 40 m près (Châtelet : jusqu'à ~120 m, tracé incomplet) ; sans gare suivante (terminus),
    coordonnées de la gare."""
    gares, geometrie, reseau = _charger(csv_gares, trace)

    @functools.lru_cache(maxsize=None)
    def troncon(a, b):
        return chemin_troncon(geometrie, reseau, a, b)

    @functools.lru_cache(maxsize=None)
    def quai(a):
        """Point unique de la gare SUR la voie. Repli sur les coordonnées de la gare si aucun passage de la ligne n'est assez proche."""
        try:
            return geometrie.point_quai(a)
        except (KeyError, NoValidDistance):
            return (gares[a].lat, gares[a].lon)

    def _trajet(a, suivante):
        """(longueur, point_a(x)) du tronçon a -> suivante : point (lat, lon) à x mètres du début, recalé sur le quai de chaque gare."""
        b = stop_area_number(suivante)
        chemin = troncon(a, b)
        longueur = sum(_dist(p, q) for p, q in zip(chemin, chemin[1:]))
        (dlat_a, dlon_a), (dlat_b, dlon_b) = ((q[0] - c[0], q[1] - c[1]) for q, c in ((quai(a), chemin[0]), (quai(b), chemin[-1])))

        def avec_decalage(x):
            point = point_a_distance(chemin, x)
            g = x / longueur if longueur > 0 else 0.0
            return (point[0] + dlat_a * (1 - g) + dlat_b * g, point[1] + dlon_a * (1 - g) + dlon_b * g)
        return longueur, avec_decalage

    def localiser(gare, suivante, distance_m):
        """Position sur le tronçon, recalée sur le quai commun de chaque gare : le chemin (choisi par paire de gares) peut finir
        à 100 m du quai où le train repart ; on le décale donc de façon progressive (début -> quai de A, fin -> quai de B) au lieu
        de laisser le train sauter à l'arrivée. Décalage nul là où le chemin passe déjà par le quai (médiane des tronçons : 4 m). Quai = passage central de la gare (Geometry.point_quai), donc sur la voie des tronçons voisins."""
        a = stop_area_number(gare)
        if suivante is None:
            return (*quai(a), None)
        longueur, avec_decalage = _trajet(a, suivante)
        d = min(max(distance_m or 0.0, 0.0), longueur)
        lat, lon = avec_decalage(d)   # à quai (d = 0) : quai de la gare, SUR la voie, sans saut au démarrage
        cap = cap_degres(avec_decalage(max(d - 4.0, 0.0)), avec_decalage(min(d + 4.0, longueur)))
        return (lat, lon, cap)

    def voie(gare, suivante, distance_m):
        """Tracé de la voie autour du train, dans le sens de la marche : [[lon, lat], ...] de d - DEMI_VOIE_M à d + DEMI_VOIE_M (borné au tronçon),
        un point tous les PAS_VOIE_M. La carte y pose chaque caisse pour que la rame suive les courbes. None au terminus (pas de tronçon)."""
        if suivante is None:
            return None
        longueur, avec_decalage = _trajet(stop_area_number(gare), suivante)
        d = min(max(distance_m or 0.0, 0.0), longueur)
        debut, fin = max(0.0, d - DEMI_VOIE_M), min(longueur, d + DEMI_VOIE_M)
        n = max(1, math.ceil((fin - debut) / PAS_VOIE_M))
        points = [avec_decalage(debut + (fin - debut) * k / n) for k in range(n + 1)]
        return [[round(lon, 6), round(lat, 6)] for lat, lon in points]
    localiser.voie = voie

    def nom_gare(ref):
        return gares[stop_area_number(ref)].name

    return distance_depuis_reseau(reseau), localiser, nom_gare


def chaine_demo(nom_depart="Massy - Palaiseau", nom_arrivee="Gare du Nord", csv_gares=CSV_GARES, trace=TRACE):
    """Suite de gares (identifiants PRIM) entre deux gares, pour la source de démonstration."""
    gares, _, reseau = _charger(csv_gares, trace)
    par_nom = {g.name: n for n, g in gares.items()}
    return [f"STIF:StopArea:SP:{n}:" for n in reseau.chemin(par_nom[nom_depart], par_nom[nom_arrivee])]


def charger_geo(csv_gares=CSV_GARES, trace=TRACE):
    """Tracé et gares pour la carte : {"trace": [[[lon, lat], ...], ...], "gares": [{ref, nom, lon, lat}]} (5 décimales ~ 1 m)."""
    morceaux = []
    for m in load_morceaux(trace):
        ligne = []
        for lat, lon in m:
            p = [round(lon, 5), round(lat, 5)]
            if not ligne or p != ligne[-1]:
                ligne.append(p)
        morceaux.append(ligne)
    gares = [{"ref": n, "nom": g.name, "lon": round(g.lon, 5), "lat": round(g.lat, 5)} for n, g in load_stops(csv_gares).items()]
    return {"trace": morceaux, "gares": gares}


def trouver_env(depart=RACINE):
    """Premier `.env` trouvé en remontant depuis `depart` (le worktree est sous le dépôt principal) ; sinon `depart/.env`."""
    for dossier in (depart, *depart.parents):
        if (dossier / ".env").is_file():
            return dossier / ".env"
    return depart / ".env"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Serveur local RER B (127.0.0.1 seulement). Appelle PRIM à la demande.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--env", default=str(trouver_env()), help="fichier .env (défaut : le premier trouvé en remontant)")
    ap.add_argument("--intervalle", type=int, default=INTERVALLE_S, help="secondes entre deux appels PRIM (minimum 60)")
    ap.add_argument("--plafond", type=int, default=PLAFOND_JOUR, help="appels PRIM maximum par jour")
    ap.add_argument("--fichier-plafond", default=str(Path.home() / ".rerb-prim-serveur-plafond.json"))
    ap.add_argument("--plafond-gares", type=int, default=PLAFOND_GARES_JOUR, help="appels stop-monitoring maximum par jour")
    ap.add_argument("--fichier-plafond-gares", default=str(Path.home() / ".rerb-prim-serveur-gares-plafond.json"))
    ap.add_argument("--demo", action="store_true", help="trains INVENTÉS, aucun appel PRIM, aucune clé lue")
    ap.add_argument("--revisions", action="store_true", help="avec --demo : PRIM « révise » les heures d'arrivée toutes les 30 s (±45 s, parfois +150 s), pour juger le lissage")
    ap.add_argument("--rampe", type=float, default=None, help="lissage : secondes d'arrivée rattrapées par seconde (défaut 0,2 ; 1,0 = suit PRIM sans lissage de vitesse)")
    a = ap.parse_args(argv)
    if a.revisions and not a.demo:
        ap.error("--revisions n'a de sens qu'avec --demo (les trains sont inventés)")
    lisseur = Lisseur(rampe=a.rampe) if a.rampe is not None else None
    distance, localiser, nom_gare = construire_contexte()
    if a.demo:
        from demo import livraison_demo, stop_monitoring_demo
        chaine = chaine_demo()
        source = lambda: livraison_demo(datetime.now(timezone.utc), chaine, distance, revisions=a.revisions)  # noqa: E731
        releveur = Releveur(source, Plafond(MagasinLocal(), 10 ** 9), intervalle_s=5, fenetre_nuit=None)   # démo : pas de coupure de nuit
        nom_ = nom_gare
        releveur_gares = ReleveurGares(
            lambda ref: stop_monitoring_demo(datetime.now(timezone.utc), ref, chaine, distance, nom_, revisions=a.revisions),
            Plafond(MagasinLocal(), 10 ** 9), intervalle_s=5)
        message = "MODE DÉMONSTRATION : trains inventés, aucun appel PRIM, aucune clé lue" + (" ; heures PRIM révisées toutes les 30 s" if a.revisions else "")
    else:
        if a.intervalle < 60 or a.plafond > 1400:
            ap.error("intervalle >= 60 s et plafond <= 1 400 par jour (quota PRIM : 1 500)")
        cle = lire_cle(a.env)
        releveur = Releveur(lambda: appeler_prim(cle), Plafond(MagasinLocal(a.fichier_plafond), a.plafond), a.intervalle)
        releveur_gares = ReleveurGares(lambda ref: appeler_prim(cle, URL_PRIM_SM_GARE.format(ref.split(":")[3])),
                                       Plafond(MagasinLocal(a.fichier_plafond_gares), a.plafond_gares), intervalle_s=60)
        message = (f"carte : 1 appel PRIM au plus toutes les {a.intervalle} s, aucun appel de {NUIT_DEBUT_H:02d}:00 à {NUIT_FIN_H:02d}:00 "
                   f"(heure de Paris), plafond {a.plafond} par jour ; "
                   f"prochains trains : 1 appel par gare au plus toutes les 60 s (plafond {a.plafond_gares} par jour)")
    noms = {n: g.name for n, g in _charger(CSV_GARES, TRACE)[0].items()}
    serveur = creer_serveur(releveur, distance, localiser, nom_gare, a.port, geo=charger_geo(), demo=a.demo,
                            releveur_gares=releveur_gares, gares=noms, duree_min=fournisseur_duree_min(TABLE_DUREES), lisseur=lisseur)
    print(f"carte sur http://127.0.0.1:{serveur.server_address[1]}/  ({message})", flush=True)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("arrêt", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
