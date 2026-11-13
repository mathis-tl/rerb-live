"""Mesure (lecture seule) : part du temps de trajet perdue à cause de la gare retirée par PRIM.

Pour chaque train RER B de la collecte (relevés PRIM toutes les 2 min), on rejoue estimer() toutes les
5 s avec ce qu'on savait à cet instant (dernier relevé reçu avant t) et on compare à une RÉFÉRENCE après
coup : le train reconstruit avec la dernière estimation connue de chacune de ses gares (hypothèse [H],
pas de vérité terrain). Trois lectures par instant :
• référence : train complet, vu après coup ;
• sans mémoire : seul le dernier relevé (comportement actuel de position.py) ;
• avec mémoire (simulée) : on garde la dernière heure connue des gares que PRIM a retirées.
On ne compte que les instants où la référence dit « à quai » ou « en route ».
Usage : python3 -I perte_gare_retiree.py [dossier_prim] [nom_de_train_a_detailler]
"""
import base64
import bisect
import gzip
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))

from position import Etat, Train, estimer, lire_train  # noqa: E402

PAS = timedelta(seconds=5)
JAMAIS_PERIME = timedelta(days=3650)
FACTICE = lambda a, b: 1.0  # noqa: E731  (on ne compare que les états, pas les distances)


def releves(dossier):
    for f in sorted(Path(dossier).glob("*/*.jsonl.gz")):
        try:
            with gzip.open(f, "rt") as fh:
                lignes = fh.readlines()
        except (EOFError, OSError):
            continue
        for l in lignes:
            r = json.loads(l)
            if str(r["status"]) != "200":
                continue
            c = base64.b64decode(r["body_b64"])
            try:
                c = gzip.decompress(c)
            except OSError:
                pass
            livraison = json.loads(c)["Siri"]["ServiceDelivery"]["EstimatedTimetableDelivery"][0]
            trains = []  # un relevé peut ne contenir aucun train (nuit) : clé absente
            for cadre in livraison.get("EstimatedJourneyVersionFrame") or []:
                trains += cadre.get("EstimatedVehicleJourney") or []
            yield datetime.fromisoformat(r["ts_recv_utc"]), trains


def cle(ej):
    r = ej.get("DatedVehicleJourneyRef")
    return r.get("value") if isinstance(r, dict) else r


def nom(ej):
    n = ej.get("VehicleJourneyName") or [{}]
    return n[0].get("value", "?")


def main():
    dossier = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "rerb-data" / "prim_estimated_timetable")
    detail = sys.argv[2] if len(sys.argv) > 2 else None
    tous = []                       # horodatages de tous les relevés
    obs = defaultdict(dict)         # train -> {ts: (Train brut, Train avec mémoire)}
    memoire = {}                    # train -> {gare: Passage}
    noms = {}
    for ts, trains in releves(dossier):
        tous.append(ts)
        for ej in trains:
            k = cle(ej)
            if k is None:
                continue
            noms[k] = nom(ej)
            t = lire_train(ej)
            m = memoire.setdefault(k, {})
            for p in t.passages:
                m[p.gare] = p
            obs[k][ts] = (t, Train(list(m.values()), t.recorded_at))
    print(f"{len(tous)} relevés du {tous[0]} au {tous[-1]} ; {len(obs)} trains distincts")

    ecartes = Counter()
    secondes = defaultdict(Counter)           # (état de référence, variante) -> {issue: secondes}
    par_duree = defaultdict(lambda: defaultdict(Counter))  # tranche de durée -> variante -> issue
    trains_perte = {"sans": set(), "avec": set()}
    n_trains = 0
    pas_s = PAS.total_seconds()
    for k, o in obs.items():
        ref = Train(list(memoire[k].values()), None)
        if any(p.annule for p in ref.passages):
            ecartes["train annulé"] += 1
            continue
        ts_obs = sorted(o)
        t0, t1 = ts_obs[0], min(ts_obs[-1], tous[-1])
        if estimer(ref, t0, FACTICE, JAMAIS_PERIME).etat == Etat.NON_PLACE:
            # la référence est refusée (décision D4) : heures reconstruites incohérentes ou manquantes
            ecartes["référence refusée (heures incohérentes ou manquantes)"] += 1
            continue
        n_trains += 1
        # durée de chaque tronçon (référence) pour le découpage par tranche
        t = t0
        while t <= t1:
            r = estimer(ref, t, FACTICE, JAMAIS_PERIME)
            if r.etat in (Etat.A_QUAI, Etat.EN_ROUTE):
                i = bisect.bisect_right(tous, t) - 1
                dernier = tous[i] if i >= 0 else None
                tranche = None
                if r.etat == Etat.EN_ROUTE:
                    horaires = {p.gare: p for p in ref.passages}
                    a, b = horaires[r.gare], horaires[r.gare_suivante]
                    d = (b.arrivee or b.depart) - (a.depart or a.arrivee)
                    s = d.total_seconds()
                    tranche = "moins de 90 s" if s < 90 else "90 à 150 s" if s < 150 else "150 à 240 s" if s < 240 else "plus de 240 s"
                for variante, idx in (("sans", 0), ("avec", 1)):
                    if dernier is None or dernier not in o:
                        issue = "train absent du relevé"
                    else:
                        e = estimer(o[dernier][idx], t, FACTICE, JAMAIS_PERIME)
                        issue = "même état" if e.etat == r.etat else {
                            Etat.POSITION_INCONNUE: "position inconnue",
                            Etat.NON_PLACE: "non placé",
                            Etat.TERMINE: "terminé à tort",
                        }.get(e.etat, "autre état (à quai / en route)")
                    secondes[(r.etat.name, variante)][issue] += pas_s
                    if tranche:
                        par_duree[tranche][variante][issue] += pas_s
                    if issue == "position inconnue":
                        trains_perte[variante].add(k)
            t += PAS
    print(f"{n_trains} trains évalués ; écartés : {dict(ecartes)}")
    for etat in ("EN_ROUTE", "A_QUAI"):
        print(f"\n== Référence « {etat} »")
        for variante in ("sans", "avec"):
            c = secondes[(etat, variante)]
            tot = sum(c.values())
            if not tot:
                continue
            print(f"  {variante:4} mémoire : {tot / 3600:.1f} h au total | " +
                  " | ".join(f"{i} {100 * v / tot:.1f} %" for i, v in c.most_common()))
    print("\n== Référence « EN_ROUTE » par durée du tronçon (part du temps en « position inconnue »)")
    for tr in ("moins de 90 s", "90 à 150 s", "150 à 240 s", "plus de 240 s"):
        ligne = []
        for variante in ("sans", "avec"):
            c = par_duree[tr][variante]
            tot = sum(c.values())
            if tot:
                ligne.append(f"{variante} mémoire {100 * c['position inconnue'] / tot:.1f} % de {tot / 3600:.1f} h")
        print(f"  {tr:14} : " + " | ".join(ligne))
    print(f"\ntrains avec au moins un instant en « position inconnue » : sans mémoire {len(trains_perte['sans'])}, "
          f"avec mémoire {len(trains_perte['avec'])}, sur {n_trains}")
    if detail:
        for k, nm in noms.items():
            if nm == detail:
                print(f"\ndétail {nm} ({k}) : {len(obs[k])} relevés")


if __name__ == "__main__":
    main()
