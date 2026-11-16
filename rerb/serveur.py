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
import json
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from geometry import Geometry, load_morceaux
from memoire import Memoire
from position import DistanceInconnue, Etat, Reseau, distance_depuis_reseau, estimer, lire_train
from stops import load_stops, stop_area_number
from troncon import point_sur_troncon

RACINE = Path(__file__).resolve().parents[1]
CSV_GARES = RACINE / "rerb" / "donnees" / "arrets_rerb_C01743.csv"
TRACE = RACINE / "data" / "static" / "rerb" / "rerb_trace_C01743.json"
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


def calculer_trains(releve, maintenant, distance, localiser, nom_gare):
    """Un dict par train du relevé, à l'instant `maintenant`. `localiser(estimation)` -> (lat, lon) ou lève une erreur
    de géométrie : le train reste dans la liste, sans position, avec son motif."""
    sortie = []
    for ref, (train, retards) in releve.trains.items():
        e = estimer(train, maintenant, distance)
        lat = lon = None
        motif = e.motif
        if e.etat in (Etat.A_QUAI, Etat.EN_ROUTE):
            try:
                lat, lon = localiser(e)
            except (KeyError, ValueError, DistanceInconnue) as erreur:
                motif = f"position impossible ({erreur})"
        gare_pertinente = e.gare_suivante or e.gare
        sortie.append({
            "course": ref, "etat": e.etat.value, "motif": motif,
            "gare": nom_gare(e.gare) if e.gare else None,
            "gare_suivante": nom_gare(e.gare_suivante) if e.gare_suivante else None,
            "lat": lat, "lon": lon,
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


def creer_serveur(releveur, distance, localiser, nom_gare, port=8765, horloge=None):
    """Serveur HTTP sur 127.0.0.1 seulement. `horloge()` -> instant UTC (remplaçable en test)."""
    horloge = horloge or (lambda: datetime.now(timezone.utc))

    def charge_utile():
        maintenant = horloge()
        releveur.actualiser(maintenant)
        r = releveur.releve   # copie de la référence : le relevé est immuable
        return {
            "maintenant": maintenant.isoformat(),
            "releve_recu": r.recu.isoformat() if r else None,
            "releve_age_s": int((maintenant - r.recu).total_seconds()) if r else None,
            "erreur": releveur.derniere_erreur, "bloque": releveur.bloque,
            "plafond": {"utilises": releveur.plafond.utilises, "maximum": releveur.plafond.maximum},
            "trains": calculer_trains(r, maintenant, distance, localiser, nom_gare) if r else [],
        }

    class Gestionnaire(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] not in ("/trains", "/statut"):
                self.send_error(404)
                return
            corps = charge_utile()
            if self.path.startswith("/statut"):
                corps.pop("trains")
            octets = json.dumps(corps, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(octets)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(octets)

        def log_message(self, *args):  # pas de journal d'accès sur l'écran
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Gestionnaire)


def construire_contexte(csv_gares=CSV_GARES, trace=TRACE):
    """Fichiers statiques -> (distance, localiser, nom_gare) pour `calculer_trains`. Hors ligne, aucun appel API.
    localiser(estimation) -> (lat, lon) : sur les rails (J1) ; à quai : coordonnées de la gare."""
    gares = load_stops(csv_gares)
    geometrie = Geometry(load_morceaux(trace), {n: (s.lat, s.lon) for n, s in gares.items()})
    reseau = Reseau({p: geometrie.distance(*p) for p in geometrie.neighbor_pairs()})

    def localiser(e):
        a = stop_area_number(e.gare)
        if e.etat == Etat.A_QUAI:
            return (gares[a].lat, gares[a].lon)
        return point_sur_troncon(geometrie, reseau, a, stop_area_number(e.gare_suivante), e.distance_m)

    def nom_gare(ref):
        return gares[stop_area_number(ref)].name

    return distance_depuis_reseau(reseau), localiser, nom_gare


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
    a = ap.parse_args(argv)
    if a.intervalle < 60 or a.plafond > 1400:
        ap.error("intervalle >= 60 s et plafond <= 1 400 par jour (quota PRIM : 1 500)")
    cle = lire_cle(a.env)
    distance, localiser, nom_gare = construire_contexte()
    releveur = Releveur(lambda: appeler_prim(cle), Plafond(a.fichier_plafond, a.plafond), a.intervalle)
    serveur = creer_serveur(releveur, distance, localiser, nom_gare, a.port)
    print(f"serveur sur http://127.0.0.1:{serveur.server_address[1]}/trains  (1 appel PRIM au plus toutes les "
          f"{a.intervalle} s, quand une page le demande ; plafond {a.plafond} par jour)", flush=True)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("arrêt", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
