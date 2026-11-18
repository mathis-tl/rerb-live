"""État des lieux hors ligne (lecture seule, aucun appel réseau) : où le roulage annoncé est-il faux ?

Usage : python3 -I rerb/exploration/diagnostiquer_roulage.py DOSSIER BORNES.json JOUR HEURES [--json SORTIE.json]

Quatre mesures, une tranche à la fois (comptage PAR COURSE : dernier relevé reçu avant le départ estimé de A) :
A. part sous la borne selon que A est l'ORIGINE de la course ou une gare intermédiaire ;
B. attribution (intermédiaires, sous la borne ou non) : retard au départ de A, à l'arrivée à A, à l'arrivée à B ;
C. durée d'arrêt à A, annoncée contre théorique ;
D. bruit : stabilité des catégories entre deux moitiés de courses de la MÊME tranche.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rejouer_roulage import CSV_GARES, N_MIN, lire_releves  # noqa: E402
from roulage import (  # noqa: E402
    bilan_sous_borne, categorie, mediane, mediane_theorique, observer, stabilite, une_par_course, moitie,
)
from stops import load_stops  # noqa: E402


def med(valeurs):
    v = [x for x in valeurs if x is not None]
    return mediane(v), len(v)


def categories(obs, bornes, retenus):
    b = bilan_sous_borne(obs, bornes, retenus)
    return {c: categorie(s / n) for c, (s, n) in b.par_troncon.items() if n}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("dossier", type=Path)
    p.add_argument("bornes", type=Path)
    p.add_argument("jour")
    p.add_argument("heures")
    p.add_argument("--json", type=Path)
    a = p.parse_args()
    h0, h1 = (int(x) for x in a.heures.split("-"))
    phys = json.loads(a.bornes.read_text())
    bornes = {k: v["roulage_borne_s"] for k, v in phys.items() if "roulage_borne_s" in v}
    noms = {n: s.name for n, s in load_stops(CSV_GARES).items()}

    obs, n_rel = [], 0
    for recu, courses, _ in lire_releves(a.dossier, a.jour, h0, h1):
        n_rel += 1
        obs += observer(recu, courses, tri="theorique")
    retenus = set(mediane_theorique(obs, N_MIN))
    pc = une_par_course(obs)["retenues"]
    pc = [o for o in pc if o.cle in retenus]
    out = {"dossier": str(a.dossier), "jour": a.jour, "heures": a.heures, "releves": n_rel, "courses_troncons": len(pc)}
    print(f"{a.dossier.name} {a.jour} {a.heures} h UTC : {n_rel} relevés ; {len(pc)} couples (course, tronçon) retenus")

    # A. origine contre intermédiaire
    print("\nA. part sous la borne, par course")
    out["A"] = {}
    for nom, flag in (("origine", True), ("intermédiaire", False)):
        b = bilan_sous_borne(pc, bornes, origine=flag)
        bo = bilan_sous_borne([o for o in obs if o.cle in retenus], bornes, origine=flag)
        out["A"][nom] = {"course": [b.sous, b.total], "occurrence": [bo.sous, bo.total]}
        print(f"   A {nom:14s} : {b.sous}/{b.total} = {100 * b.part:.1f} % (course) ; {bo.sous}/{bo.total} = {100 * bo.part:.1f} % (occurrence)")

    # B. attribution, intermédiaires
    print("\nB. retards en secondes (estimé moins théorique), A intermédiaire, médiane (n) par course")
    out["B"] = {}
    inter = [o for o in pc if not o.a_origine and o.annonce is not None]
    for nom, sel in (("sous la borne", True), ("au-dessus", False)):
        grp = [o for o in inter if (bornes.get(f"{o.depart_gare}|{o.arrivee_gare}") or bornes.get(f"{o.arrivee_gare}|{o.depart_gare}") or 0) > o.annonce] if sel else \
              [o for o in inter if (bornes.get(f"{o.depart_gare}|{o.arrivee_gare}") or bornes.get(f"{o.arrivee_gare}|{o.depart_gare}")) and (bornes.get(f"{o.depart_gare}|{o.arrivee_gare}") or bornes.get(f"{o.arrivee_gare}|{o.depart_gare}")) <= o.annonce]
        r = {"n": len(grp), "retard_dep_A": med(o.retard_dep for o in grp), "retard_arr_A": med(o.retard_arr_a for o in grp),
             "retard_arr_B": med(o.retard_arr_b for o in grp)}
        out["B"][nom] = r
        print(f"   {nom:14s} n={r['n']:5d} : départ A {r['retard_dep_A'][0]:+.0f} ({r['retard_dep_A'][1]}) ; arrivée A {r['retard_arr_A'][0]:+.0f} ({r['retard_arr_A'][1]}) ; arrivée B {r['retard_arr_B'][0]:+.0f} ({r['retard_arr_B'][1]})")

    # C. durées d'arrêt
    print("\nC. durée d'arrêt à A (intermédiaires), par course : annoncée contre théorique")
    arr = [o for o in inter if o.arret_est is not None and o.arret_theo is not None]
    ecarts = [o.arret_est - o.arret_theo for o in arr]
    c = Counter("annoncé plus court de 10 s ou plus" if e <= -10 else "annoncé plus long de 10 s ou plus" if e >= 10 else "à ±10 s" for e in ecarts)
    out["C"] = {"n": len(arr), "arret_est_med": med(o.arret_est for o in arr), "arret_theo_med": med(o.arret_theo for o in arr), "repartition": dict(c)}
    print(f"   n={len(arr)} ; arrêt annoncé médian {out['C']['arret_est_med'][0]:.0f} s, théorique médian {out['C']['arret_theo_med'][0]:.0f} s ; {dict(c)}")
    par_gare = {}
    for o in arr:
        par_gare.setdefault(o.depart_gare, []).append(o)
    lignes = sorted(((med(x.arret_est for x in v)[0], med(x.arret_theo for x in v)[0], len(v), g) for g, v in par_gare.items() if len(v) >= 20))
    out["C"]["par_gare"] = [{"gare": noms.get(g, g), "arret_est": e, "arret_theo": t, "n": n} for e, t, n, g in lignes]
    print("   gares (n ≥ 20) : arrêt annoncé / théorique médians")
    for e, t, n, g in lignes:
        print(f"     {noms.get(g, g)[:34]:34s} {e:5.0f} / {t:4.0f}  (n={n})")

    # E. l'estimé est-il différent du théorique ? (si égal à la seconde, aucune information temps réel)
    print("\nE. part des heures estimées EXACTEMENT égales au théorique (par course) : un estimé identique n'apporte aucune info temps réel")
    out["E"] = {}
    for nom, lst in (("A origine", [o for o in pc if o.a_origine]), ("A intermédiaire", [o for o in pc if not o.a_origine])):
        e = {}
        for champ in ("retard_dep", "retard_arr_a", "retard_arr_b"):
            v = [getattr(o, champ) for o in lst if getattr(o, champ) is not None]
            e[champ] = [sum(1 for x in v if x == 0), len(v)]
        out["E"][nom] = e
        txt = " ; ".join(f"{c} {100 * z / max(1, n):.0f} % ({n})" for c, (z, n) in e.items())
        print(f"   {nom:16s} : {txt}")

    # D. bruit entre deux moitiés de courses
    print("\nD. bruit : catégories entre deux moitiés de courses de la même tranche")
    res = {}
    for unite in ("course", "occurrence"):
        cats = []
        for k in (0, 1):
            m = moitie(obs, k)
            ret = set(mediane_theorique(m, N_MIN))
            lst = [o for o in une_par_course(m)["retenues"]] if unite == "course" else m
            cats.append(categories(lst, bornes, ret))
        communs, ident, diff = stabilite(*cats)
        res[unite] = {"communs": communs, "identiques": ident, "part": ident / communs if communs else None,
                      "retournements": sum(1 for _, x, y in diff if {x, y} == {"bas", "haut"})}
        print(f"   {unite:10s} : {ident}/{communs} = {100 * ident / max(1, communs):.1f} % identiques ; retournements bas↔haut : {res[unite]['retournements']}")
    out["D"] = res
    if a.json:
        a.json.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=list))


if __name__ == "__main__":
    main()
