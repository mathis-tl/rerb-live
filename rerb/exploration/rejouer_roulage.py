"""Rejeu hors ligne (lecture seule, aucun appel réseau) : roulages annoncés contre borne physique.

Usage : python3 -I rerb/exploration/rejouer_roulage.py DOSSIER_RELEVES BORNES.json [--heures 5-9] [--jour AAAA-MM-JJ] [--json SORTIE.json]

DOSSIER_RELEVES contient prim_estimated_timetable/<jour>/*.jsonl.gz (enregistreur du projet).
BORNES.json : sortie de roulage_physique.py ({"a|b": {"roulage_borne_s": ..., "roulage_realiste_s": ...}}).
Logique dans rerb/roulage.py (testée). Ce script ne fait que lire, décoder et imprimer.
"""
import argparse
import base64
import gzip
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from roulage import (  # noqa: E402
    bilan_sous_borne, categorie, lire_course, lire_origine, mediane, mediane_theorique, observer, rapports, une_par_course,
)
from stops import load_stops  # noqa: E402

CSV_GARES = Path(__file__).resolve().parent.parent / "donnees/arrets_rerb_C01743.csv"
N_MIN = 10  # tronçons à moins de 10 courses théoriques écartés (comme le script du council)


def lire_releves(dossier, jour, h0, h1):
    """Génère (instant de réception, liste de (ref, [Appel]), statut) par relevé ; compte les écartés."""
    ecartes = Counter()
    fichiers = []
    for h in range(h0, h1 + 1):
        fichiers += sorted((dossier / "prim_estimated_timetable").glob(f"{jour}/*T{h:02d}.jsonl.gz"))
    if not fichiers:
        sys.exit(f"aucun fichier pour {jour} {h0}-{h1} dans {dossier}")
    for f in fichiers:
        with gzip.open(f, "rt") as fh:
            for ligne in fh:
                r = json.loads(ligne)
                if str(r.get("status")) != "200" or not r.get("body_b64"):
                    ecartes["relevé non 200 ou vide"] += 1
                    continue
                corps = base64.b64decode(r["body_b64"])
                try:
                    corps = gzip.decompress(corps)
                except OSError:
                    pass
                try:
                    liv = json.loads(corps)["Siri"]["ServiceDelivery"]["EstimatedTimetableDelivery"][0]
                except (KeyError, IndexError, ValueError):
                    ecartes["relevé illisible"] += 1
                    continue
                courses = []
                for cadre in liv.get("EstimatedJourneyVersionFrame") or []:
                    for ej in cadre.get("EstimatedVehicleJourney") or []:
                        ref, appels = lire_course(ej)
                        if ref is None:
                            ecartes["course sans référence"] += 1
                            continue
                        courses.append((ref, appels, lire_origine(ej)))
                yield datetime.fromisoformat(r["ts_recv_utc"]), courses, ecartes


def main():
    p = argparse.ArgumentParser()
    p.add_argument("dossier", type=Path)
    p.add_argument("bornes", type=Path)
    p.add_argument("--heures", default="5-9", help="heures UTC de fichier, bornes incluses (défaut : 5-9, comme le council)")
    p.add_argument("--jour", default="2026-11-16")
    p.add_argument("--json", type=Path)
    a = p.parse_args()
    h0, h1 = (int(x) for x in a.heures.split("-"))

    phys = json.loads(a.bornes.read_text())
    bornes = {k: v["roulage_borne_s"] for k, v in phys.items() if "roulage_borne_s" in v}
    realistes = {k: v["roulage_realiste_s"] for k, v in phys.items() if "roulage_realiste_s" in v}
    noms = {n: s.name for n, s in load_stops(CSV_GARES).items()}

    variantes = {"council (tri Aimed, annulés gardés)": dict(tri="theorique", ecarter_annules=False),
                 "position.py (tri estimé, annulés écartés)": dict(tri="estime", ecarter_annules=True)}
    obs = {nom: [] for nom in variantes}
    n_releves = 0
    ecartes = Counter()
    for recu, courses, ecartes in lire_releves(a.dossier, a.jour, h0, h1):
        n_releves += 1
        for nom, opts in variantes.items():
            obs[nom] += observer(recu, courses, **opts)

    sortie = {"dossier": str(a.dossier), "jour": a.jour, "heures_utc": a.heures, "releves": n_releves,
              "ecartes_lecture": dict(ecartes), "variantes": {}}
    print(f"{a.dossier} {a.jour} {a.heures} h UTC : {n_releves} relevés ; écartés à la lecture : {dict(ecartes) or 'aucun'}")
    for nom, liste in obs.items():
        theo = mediane_theorique(liste, N_MIN)
        retenus = set(theo)
        occ = bilan_sous_borne(liste, bornes, retenus)
        pc = une_par_course(liste)
        crs = bilan_sous_borne(pc["retenues"], bornes, retenus)
        rap = sorted(rapports(theo, realistes))
        med = mediane(rap)
        cats = {c: categorie(s / n) for c, (s, n) in occ.par_troncon.items() if n}
        cats_c = {c: categorie(s / n) for c, (s, n) in crs.par_troncon.items() if n}
        repart = Counter(cats.values())
        repart_c = Counter(cats_c.values())
        print(f"\n== {nom} ==")
        print(f"observations avec annonce : {sum(1 for o in liste if o.annonce is not None)} ; tronçons orientés retenus (n théorique ≥ {N_MIN}) : {len(retenus)}")
        print(f"PAR OCCURRENCE : {occ.sous}/{occ.total} = {100 * occ.part:.1f} % sous la borne (sans borne : {occ.sans_borne})")
        print(f"PAR COURSE     : {crs.sous}/{crs.total} = {100 * crs.part:.1f} % sous la borne ; écartés {pc['ecartes']}")
        print(f"répartition par tronçon (occurrence) : {dict(repart)} ; (course) : {dict(repart_c)}")
        print(f"rapport théorique/réaliste : médiane {med:.2f} sur {len(rap)} tronçons")
        lignes = []
        for c in sorted(occ.par_troncon, key=lambda c: (noms.get(c[0], c[0]), noms.get(c[1], c[1]))):
            s, n = occ.par_troncon[c]
            sc, nc = crs.par_troncon.get(c, (0, 0))
            lignes.append({"depart": c[0], "arrivee": c[1], "troncon": f"{noms.get(c[0], c[0])} → {noms.get(c[1], c[1])}",
                           "occ_sous": s, "occ_n": n, "course_sous": sc, "course_n": nc,
                           "theo_med_s": theo[c][0], "theo_n": theo[c][1]})
        sortie["variantes"][nom] = {
            "occurrence": {"sous": occ.sous, "total": occ.total}, "course": {"sous": crs.sous, "total": crs.total},
            "ecartes_course": pc["ecartes"], "repartition_occ": dict(repart), "repartition_course": dict(repart_c),
            "rapport_median": med, "n_rapports": len(rap), "troncons": lignes}
        print(f"{'tronçon orienté':48s} {'occ':>12s} {'course':>10s} {'théo méd.':>9s}")
        for l in lignes:
            po = 100 * l["occ_sous"] / l["occ_n"]
            pcs = 100 * l["course_sous"] / l["course_n"] if l["course_n"] else float("nan")
            print(f"{l['troncon'][:48]:48s} {po:6.0f} % ({l['occ_n']:5d}) {pcs:6.0f} % ({l['course_n']:3d}) {l['theo_med_s']:7.0f}")
    if a.json:
        a.json.write_text(json.dumps(sortie, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
