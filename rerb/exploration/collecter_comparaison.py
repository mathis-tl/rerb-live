"""Collecte du test de comparaison estimated-timetable (ET) / stop-monitoring par gare (SM). Appels PRIM RÉELS.
À chaque tour (période `--periode` s) : 1 appel ET + 1 appel SM par gare du CSV (47 gares), à la suite, avec une pause courte.
Réponses brutes -> ~/rerb-data-3/comparaison-<horodatage>/ (hors dépôt). Garde-fous : nombre d'appels borné (--max-et, --max-sm),
arrêt immédiat sur 401/403/429, aucune clé dans les fichiers ni dans la sortie. Aucune autre sortie que des comptes.
Usage : python3 rerb/exploration/collecter_comparaison.py [--tours 10] [--periode 60] [--max-et 15] [--max-sm 520]"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from serveur import (URL_PRIM_ET, URL_PRIM_SM_GARE, ErreurPrim, appeler_prim, charger_geo, lire_cle,  # noqa: E402
                     trouver_env)

ap = argparse.ArgumentParser()
ap.add_argument("--tours", type=int, default=10)
ap.add_argument("--periode", type=int, default=60)
ap.add_argument("--max-et", type=int, default=15)
ap.add_argument("--max-sm", type=int, default=520)
ap.add_argument("--pause", type=float, default=0.25)
a = ap.parse_args()

cle = lire_cle(trouver_env())
refs = sorted(g["ref"] for g in charger_geo()["gares"])
sortie = Path.home() / "rerb-data-3" / ("comparaison-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
sortie.mkdir(parents=True)
n_et = n_sm = erreurs = 0


def appel(url, nom, meta):
    global erreurs
    debut = datetime.now(timezone.utc)
    try:
        corps = appeler_prim(cle, url)
        err = None
    except ErreurPrim as e:
        corps, err = None, str(e)
        erreurs += 1
        if e.statut in (401, 403, 429):
            print(f"ARRÊT : {e}", flush=True)
            sys.exit(2)
    (sortie / nom).write_text(json.dumps({"ts_recv": debut.isoformat(), "duree_s": (datetime.now(timezone.utc) - debut).total_seconds(),
                                         "erreur": err, **meta, "body": corps}, ensure_ascii=False), encoding="utf-8")


print(f"collecte vers {sortie}, {a.tours} tours de {a.periode} s, {len(refs)} gares", flush=True)
for tour in range(a.tours):
    t0 = time.monotonic()
    if n_et < a.max_et:
        appel(URL_PRIM_ET, f"et_{tour:02d}.json", {"source": "et", "tour": tour})
        n_et += 1
    for ref in refs:
        if n_sm >= a.max_sm:
            break
        appel(URL_PRIM_SM_GARE.format(ref), f"sm_{tour:02d}_{ref}.json", {"source": "sm", "tour": tour, "gare": ref})
        n_sm += 1
        time.sleep(a.pause)
    print(f"tour {tour + 1}/{a.tours} fini : ET {n_et}, SM {n_sm}, erreurs {erreurs}, {time.monotonic() - t0:.0f} s", flush=True)
    reste = a.periode - (time.monotonic() - t0)
    if tour + 1 < a.tours and reste > 0:
        time.sleep(reste)
print("collecte terminée", flush=True)
