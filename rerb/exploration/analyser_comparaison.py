"""Analyse d'une collecte de comparaison ET / SM (voir collecter_comparaison.py).
Usage : python3 -I rerb/exploration/analyser_comparaison.py <dossier de collecte>     (données lues avec -I)
Mesure FRAÎCHEUR, ACCORD et COHÉRENCE des deux sources. Ce n'est PAS une mesure de justesse (aucune vérité terrain ici)."""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comparaison import (age_s, changements, ecarts_et_sm, passages_et, passages_sm, quantile,  # noqa: E402
                         segments_vitesse)
from memoire import Memoire  # noqa: E402
from serveur import construire_contexte  # noqa: E402

dossier = Path(sys.argv[1])
distance, _, nom_gare = construire_contexte()
nom = lambda ref: nom_gare(ref)  # noqa: E731


def charger(fichier):
    return json.loads(fichier.read_text(encoding="utf-8"))


def ts(d):
    return datetime.fromisoformat(d["ts_recv"])


tours = sorted({int(f.name.split("_")[1].split(".")[0]) for f in dossier.glob("et_*.json")})
et_par_tour, sm_par_tour, age_et, age_sm, erreurs = {}, {}, [], [], {"et": 0, "sm": 0}
fautifs = {"et": {}, "sm": {}}
n_seg = {"et": 0, "sm": 0}
n_mauv = {"et": 0, "sm": 0}
ecarts_tous, ecarts_100 = [], []
sans_et = sans_sm = 0
for t in tours:
    f_et = charger(dossier / f"et_{t:02d}.json")
    if f_et["erreur"]:
        erreurs["et"] += 1
        continue
    passages = passages_et(f_et["body"], Memoire())
    recu_et = ts(f_et)
    # ET : on ne garde que le futur proche (même fenêtre que SM : passages à venir ou partis depuis moins d'une minute)
    et = [x for x in passages if (x["depart"] or x["arrivee"]).timestamp() > recu_et.timestamp() - 60]
    sm = []
    for f in sorted(dossier.glob(f"sm_{t:02d}_*.json")):
        d = charger(f)
        if d["erreur"]:
            erreurs["sm"] += 1
            continue
        for x in passages_sm(d["body"]):
            x["recu"] = ts(d)
            sm.append(x)
    et_par_tour[t], sm_par_tour[t] = et, sm
    age_et += [a for a in (age_s(recu_et, x["recorded_at"]) for x in et) if a is not None]
    age_sm += [a for a in (age_s(x["recu"], x["recorded_at"]) for x in sm) if a is not None]
    # accord sur les (course, gare) communs
    ec = ecarts_et_sm(et, sm)
    ecarts_tous += ec
    # cohérence (vitesses implicites) sur les passages à venir de chaque source
    for src, ps in (("et", et), ("sm", sm)):
        n, mauvais, _ = segments_vitesse(ps, distance)
        n_seg[src] += n
        n_mauv[src] += len(mauvais)
        for m in mauvais:
            k = (m["de"], m["vers"])
            fautifs[src][k] = fautifs[src].get(k, 0) + 1
    cles_et = {(x["course"], x["gare"]) for x in et}
    cles_sm = {(x["course"], x["gare"]) for x in sm}
    sans_sm += len(cles_et - cles_sm)
    sans_et += len(cles_sm - cles_et)


def fmt(v, u="s"):
    return "n/a" if v is None else f"{v:.0f} {u}"


print(f"tours analysés : {len(tours)} ; erreurs ET {erreurs['et']}, SM {erreurs['sm']}")
print(f"passages par tour (futur proche) : ET médiane {quantile([len(v) for v in et_par_tour.values()], .5)}, "
      f"SM médiane {quantile([len(v) for v in sm_par_tour.values()], .5)}")
print(f"(course, gare) dans ET seulement : {sans_sm} ; dans SM seulement : {sans_et} (cumul des tours)")
print(f"ACCORD sur {len(ecarts_tous)} (course, gare) communs : SM - ET, médiane {fmt(quantile(ecarts_tous, .5))}, "
      f"|écart| médian {fmt(quantile([abs(e) for e in ecarts_tous], .5))}, p90 {fmt(quantile([abs(e) for e in ecarts_tous], .9))}, "
      f"|écart| ≤ 5 s : {sum(abs(e) <= 5 for e in ecarts_tous) / max(1, len(ecarts_tous)):.0%}, > 60 s : "
      f"{sum(abs(e) > 60 for e in ecarts_tous) / max(1, len(ecarts_tous)):.0%}")
print(f"FRAÎCHEUR (réception - RecordedAtTime) : ET médiane {fmt(quantile(age_et, .5))} p90 {fmt(quantile(age_et, .9))} ; "
      f"SM médiane {fmt(quantile(age_sm, .5))} p90 {fmt(quantile(age_sm, .9))}")
for src in ("et", "sm"):
    suivis, changes = changements([et_par_tour[t] if src == "et" else sm_par_tour[t] for t in tours if t in et_par_tour])
    print(f"RENOUVELLEMENT {src.upper()} : {changes}/{suivis} (course, gare) changent d'un relevé au suivant ({changes / max(1, suivis):.0%})")
    print(f"COHÉRENCE {src.upper()} : {n_mauv[src]}/{n_seg[src]} segments > 33 m/s ({n_mauv[src] / max(1, n_seg[src]):.1%})")
    for (a, b), n in sorted(fautifs[src].items(), key=lambda kv: -kv[1])[:4]:
        try:
            print(f"    {n}× {nom(a)} -> {nom(b)}")
        except (KeyError, ValueError):
            print(f"    {n}× {a} -> {b}")
