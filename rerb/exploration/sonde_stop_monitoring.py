"""SONDE (tâche 4, test court) : `stop-monitoring` PRIM sur quelques gares du RER B, toutes les 10 s.

ATTENTION : fait de VRAIS appels PRIM (clé `PRIM_API_KEY` lue dans .env, jamais affichée ni écrite). À lancer
seulement avec l'accord de Mathis. Plafond dur : 400 appels ; arrêt au premier 401, 403 ou 429, ou à 3 erreurs.
Usage : python3 -I sonde_stop_monitoring.py SORTIE.json [--essai] [--minutes=10] [--pas=10]
(--essai : un seul appel sur Massy - Palaiseau pour vérifier la clé.)

Mesure : (a) chaque gare répond-elle comme Massy (200, passages du RER B) ? (b) à quelle fréquence le contenu change
(`ResponseTimestamp` exclu) et quel âge a `RecordedAtTime` ? Sortie : résumé par appel, sans clé ni en-tête secret.
Les données restent dans SORTIE.json (hors dépôt), aucune publication (licence PRIM ambiguë).
"""
import gzip
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))

from stops import load_stops  # noqa: E402

ENV = Path("/Users/mathistelle/projets/rerb-live/.env")
CSV = RACINE / "rerb" / "donnees" / "arrets_rerb_C01743.csv"
URL = "https://prim.iledefrance-mobilites.fr/marketplace/stop-monitoring?MonitoringRef="
GARES = ["Massy - Palaiseau", "Antony", "Châtelet - Les Halles", "Gare du Nord", "Aulnay-sous-Bois", "Orsay - Ville"]
LIGNE = "STIF:Line::C01743:"
PLAFOND = 400


def cle_prim():
    for ligne in ENV.read_text().splitlines():
        if ligne.startswith("PRIM_API_KEY="):
            return ligne.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("PRIM_API_KEY absente de .env")


def valeur(x):
    return x.get("value") if isinstance(x, dict) else x


def appeler(numero, cle):
    ref = urllib.parse.quote(f"STIF:StopArea:SP:{numero}:", safe="")
    req = urllib.request.Request(URL + ref, headers={"apikey": cle, "Accept-Encoding": "gzip",
                                                     "User-Agent": "rerb-live-sonde/0.1"})
    debut = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            corps, statut, entetes = r.read(), r.status, r.headers
    except urllib.error.HTTPError as e:
        return {"statut": e.code, "duree": time.time() - debut, "limites": {}}, None
    except Exception as e:  # réseau
        return {"statut": None, "erreur": type(e).__name__, "duree": time.time() - debut, "limites": {}}, None
    if entetes.get("Content-Encoding") == "gzip":
        corps = gzip.decompress(corps)
    limites = {k.lower(): v for k, v in entetes.items() if k.lower().startswith("x-ratelimit")}
    return {"statut": statut, "duree": time.time() - debut, "limites": limites}, json.loads(corps)


def resumer(donnees):
    livraison = donnees["Siri"]["ServiceDelivery"]
    visites = livraison["StopMonitoringDelivery"][0].get("MonitoredStopVisit") or []
    rerb = {}
    for v in visites:
        j = v["MonitoredVehicleJourney"]
        if valeur(j.get("LineRef")) != LIGNE:
            continue
        ref = valeur(j["FramedVehicleJourneyRef"]["DatedVehicleJourneyRef"])
        c = j["MonitoredCall"]
        rerb[ref] = {"arr": c.get("ExpectedArrivalTime"), "dep": c.get("ExpectedDepartureTime"),
                     "quai": bool(c.get("VehicleAtStop")), "rec": v.get("RecordedAtTime")}
    empreinte = hashlib.sha256(json.dumps(rerb, sort_keys=True).encode()).hexdigest()[:12]
    return {"horodatage_reponse": livraison.get("ResponseTimestamp"), "n_total": len(visites), "n_rerb": len(rerb),
            "empreinte": empreinte, "trains": rerb}


def main():
    args = sys.argv[1:]
    sortie = Path(args[0])
    essai = "--essai" in args
    minutes = float(next((a.split("=")[1] for a in args if a.startswith("--minutes=")), 10))
    pas = float(next((a.split("=")[1] for a in args if a.startswith("--pas=")), 10))
    stops = load_stops(CSV)
    par_nom = {s.name: n for n, s in stops.items()}
    gares = [(nom, par_nom[nom]) for nom in GARES if nom in par_nom]
    if len(gares) != len(GARES):
        raise SystemExit(f"gares introuvables : {set(GARES) - {n for n, _ in gares}}")
    cle = cle_prim()
    if essai:
        gares, tours = gares[:1], 1
    else:
        tours = int(minutes * 60 / pas)
        if tours * len(gares) > PLAFOND:
            raise SystemExit(f"{tours * len(gares)} appels prévus, plafond {PLAFOND}")
    journal, appels, erreurs = [], 0, 0
    for tour in range(tours):
        debut_tour = time.time()
        for nom, numero in gares:
            meta, donnees = appeler(numero, cle)
            appels += 1
            entree = {"gare": nom, "recu": datetime.now(timezone.utc).isoformat(), **meta}
            if donnees is not None:
                entree.update(resumer(donnees))
            journal.append(entree)
            if meta["statut"] in (401, 403, 429):
                print(f"ARRÊT : statut {meta['statut']} à l'appel {appels} ({nom})")
                sortie.write_text(json.dumps(journal, ensure_ascii=False))
                return
            if meta["statut"] != 200:
                erreurs += 1
                if erreurs >= 3:
                    print("ARRÊT : 3 erreurs")
                    sortie.write_text(json.dumps(journal, ensure_ascii=False))
                    return
            time.sleep(1.0)
        sortie.write_text(json.dumps(journal, ensure_ascii=False))
        reste = pas - (time.time() - debut_tour)
        if tour + 1 < tours and reste > 0:
            time.sleep(reste)
    print(f"{appels} appels, {erreurs} erreurs ; sortie dans {sortie}")
    dernier = next((e for e in reversed(journal) if e.get("limites")), None)
    if dernier:
        print("limites (dernier appel) :", dernier["limites"])


if __name__ == "__main__":
    main()
