"""Tâche 4, J3 : serveur local. Cache du dernier relevé PRIM `estimated-timetable`, position recalculée à chaque requête.

Plan : docs/plan-tache-4.md (J3). Décision S3 (Mathis, 16/11/2026) : au plus 1 appel toutes les 120 s, seulement quand
une requête arrive (carte ouverte), plafond de 1 000 appels par jour (quota PRIM : 1 500).
• consolider_livraison : réponse PRIM -> {course: (Train consolidé par la mémoire M2, retards par gare)}.
• calculer_trains      : relevé + instant -> liste de dicts JSON (état, gare, lat, lon, retard, périmé, âge). Aucun train masqué.
• Plafond              : compteur d'appels du jour, sauvegardé sur disque.
• Releveur             : appelle la source si le cache est trop vieux, plafond non atteint, clé non refusée ; un seul appel
                         à la fois ; une erreur garde le dernier relevé valide. 401, 403, 429 : plus aucun appel.
• creer_serveur        : http.server sur 127.0.0.1 seulement. Routes : /trains, /statut. Le reste : 404.
La clé PRIM est lue dans `.env` (lire_cle), jamais affichée, jamais écrite dans une réponse ni un message d'erreur.
Bibliothèque standard seulement. Les tests n'appellent jamais le réseau (fausse source).
"""
import argparse
import functools
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from geometry import Geometry, load_morceaux, point_a_distance
from memoire import Memoire
from position import DistanceInconnue, Etat, Reseau, distance_depuis_reseau, estimer, lire_train
from stops import load_stops, stop_area_number
from troncon import cap_degres, chemin_troncon

RACINE = Path(__file__).resolve().parents[1]
CSV_GARES = RACINE / "rerb" / "donnees" / "arrets_rerb_C01743.csv"
TRACE = RACINE / "data" / "static" / "rerb" / "rerb_trace_C01743.json"
WEB = RACINE / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".glb": "model/gltf-binary", ".txt": "text/plain; charset=utf-8"}
# Le navigateur lui-même refuse toute requête hors de ce serveur (aucun CDN, aucune tuile, aucun envoi à un tiers).
EN_TETES = {
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; "
                                "font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
INTERVALLE_S = 120
PLAFOND_JOUR = 1000
STATUTS_CLE_REFUSEE = (401, 403, 429)
URL_PRIM_ET = "https://prim.iledefrance-mobilites.fr/marketplace/estimated-timetable?LineRef=STIF%3ALine%3A%3AC01743%3A"


class ErreurPrim(Exception):
    """Échec d'un appel PRIM ; `statut` = code HTTP si connu (jamais d'information secrète dans le message)."""

    def __init__(self, message, statut=None):
        super().__init__(message)
        self.statut = statut


@dataclass(frozen=True)
class Releve:
    """Instantané IMMUABLE du dernier relevé : le fil qui interroge PRIM le remplace d'un coup (une seule affectation)."""
    recu: datetime
    trains: dict  # course -> (Train consolidé, {gare: retard en secondes})


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


def calculer_trains(releve, maintenant, distance, localiser, nom_gare):
    """Un dict par train du relevé, à l'instant `maintenant`. `localiser(gare, gare_suivante, distance_m)` ->
    (lat, lon, cap en degrés ou None) ; il peut lever une erreur de géométrie : le train reste dans la liste,
    sans position, avec son motif. À quai, `gare_suivante` donne l'orientation de la rame (None au terminus)."""
    sortie = []
    for ref, (train, retards) in releve.trains.items():
        e = estimer(train, maintenant, distance)
        lat = lon = cap = None
        motif = e.motif
        if e.etat in (Etat.A_QUAI, Etat.EN_ROUTE):
            suivante = e.gare_suivante if e.etat == Etat.EN_ROUTE else _gare_suivante(train, e.gare)
            try:
                lat, lon, cap = localiser(e.gare, suivante, e.distance_m)
            except (KeyError, ValueError, DistanceInconnue) as erreur:
                motif = f"position impossible ({erreur})"
        gare_pertinente = e.gare_suivante or e.gare
        sortie.append({
            "course": ref, "etat": e.etat.value, "motif": motif,
            "gare": nom_gare(e.gare) if e.gare else None,
            "gare_suivante": nom_gare(e.gare_suivante) if e.gare_suivante else None,
            "lat": lat, "lon": lon, "cap": cap,
            "retard_s": retards.get(gare_pertinente) if gare_pertinente else None,
            "perime": e.perime, "reconstruit": e.reconstruit, "repare": e.repare,
            "age_s": None if train.recorded_at is None else int((maintenant - train.recorded_at).total_seconds()),
        })
    return sortie


class Plafond:
    """Nombre d'appels PRIM du jour, sauvegardé dans `fichier` ; repart de 0 chaque jour ou si le fichier est illisible."""

    def __init__(self, fichier, maximum, aujourd_hui=date.today):
        self._fichier, self.maximum, self._aujourd_hui = Path(fichier), maximum, aujourd_hui
        self._jour, self._n = self._aujourd_hui(), 0
        try:
            d = json.loads(self._fichier.read_text(encoding="utf-8"))
            if d["jour"] == self._jour.isoformat():
                self._n = int(d["n"])
        except (OSError, ValueError, KeyError, TypeError):
            pass

    @property
    def utilises(self):
        self._verifier_jour()
        return self._n

    def _verifier_jour(self):
        if self._aujourd_hui() != self._jour:
            self._jour, self._n = self._aujourd_hui(), 0

    def autorise(self):
        return self.utilises < self.maximum

    def compter(self):
        self._verifier_jour()
        self._n += 1
        try:
            self._fichier.write_text(json.dumps({"jour": self._jour.isoformat(), "n": self._n}), encoding="utf-8")
        except OSError:
            pass  # le compteur en mémoire reste valable pour ce démarrage


class Releveur:
    """Cache du dernier relevé. `fetch()` -> livraison PRIM décodée, ou lève ErreurPrim."""

    def __init__(self, fetch, plafond, intervalle_s=INTERVALLE_S):
        self._fetch, self._plafond, self._intervalle = fetch, plafond, timedelta(seconds=intervalle_s)
        self._verrou = threading.Lock()
        self._memoire = Memoire()
        self.releve = None
        self.derniere_erreur = None
        self.bloque = False
        self._derniere_tentative = None

    @property
    def plafond(self):
        return self._plafond

    def actualiser(self, maintenant):
        """-> 'ok', 'trop tôt', 'plafond', 'bloqué', 'occupé' ou 'erreur'. Au plus un appel en cours."""
        if not self._verrou.acquire(blocking=False):
            return "occupé"
        try:
            if self.bloque:
                return "bloqué"
            if self._derniere_tentative is not None and maintenant - self._derniere_tentative < self._intervalle:
                return "trop tôt"
            if not self._plafond.autorise():
                return "plafond"
            self._derniere_tentative = maintenant
            self._plafond.compter()
            try:
                livraison = self._fetch()
                trains = consolider_livraison(livraison, self._memoire)
            except ErreurPrim as erreur:
                self.derniere_erreur = str(erreur)
                self.bloque = erreur.statut in STATUTS_CLE_REFUSEE
                return "erreur"
            except (KeyError, IndexError, TypeError, ValueError) as erreur:
                self.derniere_erreur = f"réponse illisible ({type(erreur).__name__})"
                return "erreur"
            self.releve = Releve(maintenant, trains)   # une seule affectation : les requêtes lisent une copie cohérente
            self.derniere_erreur = None
            return "ok"
        finally:
            self._verrou.release()


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


def appeler_prim(cle, url=URL_PRIM_ET, delai=30):
    """Un appel GET PRIM -> JSON décodé. Le message d'erreur ne contient ni la clé ni les en-têtes."""
    requete = urllib.request.Request(url, headers={"apikey": cle, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(requete, timeout=delai) as r:
            return json.loads(r.read())["Siri"]["ServiceDelivery"]
    except urllib.error.HTTPError as e:
        raise ErreurPrim(f"HTTP {e.code}", e.code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ErreurPrim(f"réseau ({type(e).__name__})") from None
    except (ValueError, KeyError) as e:
        raise ErreurPrim(f"réponse illisible ({type(e).__name__})") from None


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
                  demo=False):
    """Serveur HTTP sur 127.0.0.1 seulement. `horloge()` -> instant UTC (remplaçable en test).
    Routes : /trains, /statut, /geo (tracé et gares), fichiers de `racine_web` (défaut : web/). Le reste : 404.
    demo=True : les trains sont INVENTÉS ; la réponse porte `"demo": true` pour que la page l'annonce."""
    horloge = horloge or (lambda: datetime.now(timezone.utc))
    racine_web = Path(racine_web) if racine_web is not None else WEB

    def charge_utile():
        maintenant = horloge()
        releveur.actualiser(maintenant)
        r = releveur.releve   # copie de la référence : le relevé est immuable
        return {
            "maintenant": maintenant.isoformat(), "demo": demo,
            "releve_recu": r.recu.isoformat() if r else None,
            "releve_age_s": int((maintenant - r.recu).total_seconds()) if r else None,
            "erreur": releveur.derniere_erreur, "bloque": releveur.bloque,
            "plafond": {"utilises": releveur.plafond.utilises, "maximum": releveur.plafond.maximum},
            "trains": calculer_trains(r, maintenant, distance, localiser, nom_gare) if r else [],
        }

    class Gestionnaire(BaseHTTPRequestHandler):
        def end_headers(self):   # en-têtes de sécurité sur TOUTES les réponses, erreurs comprises
            for cle, valeur in EN_TETES.items():
                self.send_header(cle, valeur)
            super().end_headers()

        def _envoyer(self, octets, type_mime):
            self.send_response(200)
            self.send_header("Content-Type", type_mime)
            self.send_header("Content-Length", str(len(octets)))
            self.end_headers()
            self.wfile.write(octets)

        def do_GET(self):
            route = self.path.split("?")[0]
            if route in ("/trains", "/statut"):
                corps = charge_utile()
                if route == "/statut":
                    corps.pop("trains")
                self._envoyer(json.dumps(corps, ensure_ascii=False).encode("utf-8"), TYPES[".json"])
            elif route == "/geo" and geo is not None:
                self._envoyer(json.dumps(geo, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), TYPES[".json"])
            else:
                f = fichier_statique(racine_web, self.path)
                if f is None:
                    self.send_error(404)
                    return
                self._envoyer(f.read_bytes(), TYPES[f.suffix.lower()])

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
    localiser(gare, gare_suivante, distance_m) -> (lat, lon, cap) : sur les rails (J1) ; à quai : coordonnées de la gare."""
    gares, geometrie, reseau = _charger(csv_gares, trace)

    @functools.lru_cache(maxsize=None)
    def troncon(a, b):
        return chemin_troncon(geometrie, reseau, a, b)

    def localiser(gare, suivante, distance_m):
        a = stop_area_number(gare)
        if suivante is None:
            return (gares[a].lat, gares[a].lon, None)
        chemin = troncon(a, stop_area_number(suivante))
        d = distance_m or 0.0
        cap = cap_degres(point_a_distance(chemin, max(d - 4.0, 0.0)), point_a_distance(chemin, d + 4.0))
        if d == 0.0:   # à quai : position de la gare
            return (gares[a].lat, gares[a].lon, cap)
        lat, lon = point_a_distance(chemin, d)
        return (lat, lon, cap)

    def nom_gare(ref):
        return gares[stop_area_number(ref)].name

    return distance_depuis_reseau(reseau), localiser, nom_gare


def chaine_demo(nom_depart="Massy - Palaiseau", nom_arrivee="Gare du Nord", csv_gares=CSV_GARES, trace=TRACE):
    """Suite de gares (identifiants PRIM) entre deux gares, pour la source de démonstration."""
    gares, _, reseau = _charger(csv_gares, trace)
    par_nom = {g.name: n for n, g in gares.items()}
    return [f"STIF:StopArea:SP:{n}:" for n in reseau.chemin(par_nom[nom_depart], par_nom[nom_arrivee])]


def charger_geo(csv_gares=CSV_GARES, trace=TRACE):
    """Tracé et gares pour la carte : {"trace": [[[lon, lat], ...], ...], "gares": [{nom, lon, lat}]} (5 décimales ~ 1 m)."""
    morceaux = []
    for m in load_morceaux(trace):
        ligne = []
        for lat, lon in m:
            p = [round(lon, 5), round(lat, 5)]
            if not ligne or p != ligne[-1]:
                ligne.append(p)
        morceaux.append(ligne)
    gares = [{"nom": g.name, "lon": round(g.lon, 5), "lat": round(g.lat, 5)} for g in load_stops(csv_gares).values()]
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
    ap.add_argument("--demo", action="store_true", help="trains INVENTÉS, aucun appel PRIM, aucune clé lue")
    a = ap.parse_args(argv)
    distance, localiser, nom_gare = construire_contexte()
    if a.demo:
        from demo import livraison_demo
        chaine = chaine_demo()
        source = lambda: livraison_demo(datetime.now(timezone.utc), chaine, distance)  # noqa: E731
        releveur = Releveur(source, Plafond(os.devnull, 10 ** 9), intervalle_s=5)
        message = "MODE DÉMONSTRATION : trains inventés, aucun appel PRIM, aucune clé lue"
    else:
        if a.intervalle < 60 or a.plafond > 1400:
            ap.error("intervalle >= 60 s et plafond <= 1 400 par jour (quota PRIM : 1 500)")
        cle = lire_cle(a.env)
        releveur = Releveur(lambda: appeler_prim(cle), Plafond(a.fichier_plafond, a.plafond), a.intervalle)
        message = (f"1 appel PRIM au plus toutes les {a.intervalle} s, quand une page le demande ; "
                   f"plafond {a.plafond} par jour")
    serveur = creer_serveur(releveur, distance, localiser, nom_gare, a.port, geo=charger_geo(), demo=a.demo)
    print(f"carte sur http://127.0.0.1:{serveur.server_address[1]}/  ({message})", flush=True)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("arrêt", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
