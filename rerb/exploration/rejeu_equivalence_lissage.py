"""Rejeu d'équivalence du lissage (T3, 24/11/2026) : ancien comportement contre nouveau, sur les mêmes relevés de Palaiseau.

  ANCIEN  : rerb/exploration/serveur_avant_t3.py + lissage_avant_t3.py (état modifié à CHAQUE requête), requête toutes les 3 s.
  NOUVEAU : rerb/serveur.py + rerb/lissage.py (état avancé UNE fois par relevé reçu, lecture seule ensuite), requête toutes les 3 s.

Les deux passes voient les mêmes relevés, aux mêmes instants. LECTURE SEULE : aucun appel PRIM, rien n'est écrit.

Seuils FIXÉS AVANT de regarder les résultats (TICKET-T3, ne pas ajuster) :
  • écart d'arrivée adoptée ancien/nouveau : au plus 3 s pour 99 % des couples (course, gare, instant), et au plus 10 s au maximum ;
  • sauts de position sur Palaiseau : au plus 2 (valeur du 18/11) ;
  • erreur médiane de l'heure de départ affichée à Palaiseau : au plus 51 s (valeur du 18/11).
Un seuil non atteint : le script se termine par le code 1 et le rapport chiffré est l'état final. Aucun repli n'est implémenté ici.

Usage : python3 rerb/exploration/rejeu_equivalence_lissage.py [dossier_des_donnees]
        (défaut : ~/rerb-data-palaiseau-20261118)
"""
import gzip
import json
import math
import statistics
import sys
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))   # rerb/ : serveur, lissage, memoire, position...
sys.path.insert(0, str(HERE))          # exploration/ : serveur_avant_t3, lissage_avant_t3, rejouer_palaiseau

import serveur  # noqa: E402
import serveur_avant_t3  # noqa: E402
from lissage import Lisseur  # noqa: E402
from lissage_avant_t3 import Lisseur as LisseurAvant  # noqa: E402
from memoire import Memoire  # noqa: E402
from rejouer_palaiseau import PALAISEAU, PAS_S, courses_des_missions, lire_releves, lire_verite, sauts, sejours  # noqa: E402
from serveur import Releve, consolider_livraison  # noqa: E402

SEUIL_ECART_99_S = 3.0
SEUIL_ECART_MAX_S = 10.0
SEUIL_SAUTS = 2
SEUIL_DEPART_MEDIAN_S = 51.0


class Espion:
    """Enveloppe un Lisseur (ancien ou nouveau) : retient l'arrivée adoptée renvoyée par `appliquer` à chaque instant."""

    def __init__(self, lisseur):
        self._lisseur = lisseur
        self.adoptees = {}   # (instant, course, gare) -> arrivée adoptée

    def __getattr__(self, nom):   # purger, avancer, modifie, suivi_s, exporter : délégués tels quels
        return getattr(self._lisseur, nom)

    def appliquer(self, ref, train, maintenant, **kw):   # kw : `recu=` pour l'ancien, absent pour le nouveau
        sortie = self._lisseur.appliquer(ref, train, maintenant, **kw)
        for p in sortie.passages:
            self.adoptees[(maintenant, ref, p.gare)] = p.arrivee
        return sortie


def rejouer(nouveau, releves, debut, fin, distance, localiser, nom):
    """Une passe à pas de PAS_S. -> (trace au format de rejouer_palaiseau, Espion, tailles JSON si nouveau)."""
    module = serveur if nouveau else serveur_avant_t3
    espion = Espion(Lisseur() if nouveau else LisseurAvant())
    memoire, tailles = Memoire(), []
    trace, i, courant, vu, t = {}, 0, None, None, debut
    change = False
    ancien_incertitude = (serveur._incertitude, serveur_avant_t3._incertitude)
    serveur._incertitude = serveur_avant_t3._incertitude = lambda *a, **k: None   # non utilisé ici : gain de temps
    try:
        while t <= fin:
            while i < len(releves) and releves[i][0] <= t:      # toute la séquence est consolidée dans l'ordre (comme le Releveur)
                courant = Releve(releves[i][0], consolider_livraison(releves[i][1], memoire))
                i += 1
            change = courant is not None and courant is not vu   # le serveur ne voit que le dernier relevé au moment de la requête
            if change:
                vu = courant
                if nouveau:
                    espion.avancer({ref: tr for ref, (tr, _r) in courant.trains.items()}, courant.recu)
                    etat_l, etat_m = espion.exporter(), memoire.exporter()
                    x, y = json.dumps(etat_l, sort_keys=True).encode(), json.dumps(etat_m, sort_keys=True).encode()
                    tailles.append((courant.recu, len(x), len(gzip.compress(x)), len(y), len(gzip.compress(y)),
                                    len(etat_l["courses"]), len(etat_m["trains"])))
            if courant is not None:
                for d in module.calculer_trains(courant, t, distance, localiser, nom, lisseur=espion):
                    trace.setdefault(d["course"], []).append((t, d["etat"], d["gare"], d["lat"], d["lon"], change))
            t += timedelta(seconds=PAS_S)
    finally:
        serveur._incertitude, serveur_avant_t3._incertitude = ancien_incertitude
    return trace, espion, tailles


def comparer(ancien, nouveau):
    """Écarts (secondes) sur l'union des couples (instant, course, gare). Un couple présent d'un seul côté, ou non chiffré d'un seul
    côté, compte comme écart infini : pas de couple escamoté."""
    ecarts = []
    for cle in set(ancien) | set(nouveau):
        a, b = nouveau.get(cle), ancien.get(cle)
        if a is None and b is None:
            ecarts.append(0.0)
        elif a is None or b is None:
            ecarts.append(math.inf)
        else:
            ecarts.append(abs((a - b).total_seconds()))
    return ecarts


def fenetre(trace, debut, fin):
    return {c: [p for p in points if debut <= p[0] <= fin] for c, points in trace.items()}


def main(dossier):
    releves, verite = lire_releves(dossier), lire_verite(dossier)
    courses = courses_des_missions(releves, verite)
    distance, localiser, nom_fn = serveur.construire_contexte()
    debut_fenetre = min(v[1] for v in verite) - timedelta(minutes=12)
    fin_fenetre = max(v[2] for v in verite) + timedelta(minutes=4)
    debut, fin = releves[0][0], releves[-1][0] + timedelta(minutes=5)   # équivalence : toute la séquence enregistrée
    print(f"{len(releves)} relevés reçus du {releves[0][0]:%H:%M} au {releves[-1][0]:%H:%M} UTC ; pas de {PAS_S} s ; "
          f"{len(verite)} trains mesurés, {len(courses)} appariés ; fenêtre Palaiseau {debut_fenetre:%H:%M}-{fin_fenetre:%H:%M} UTC")

    trace_n, espion_n, tailles = rejouer(True, releves, debut, fin, distance, localiser, nom_fn)
    trace_a, espion_a, _ = rejouer(False, releves, debut, fin, distance, localiser, nom_fn)

    # 1. Équivalence : écart d'arrivée adoptée, couple (instant, course, gare)
    ecarts = comparer(espion_a.adoptees, espion_n.adoptees)
    part_3s = sum(1 for e in ecarts if e <= SEUIL_ECART_99_S) / len(ecarts)
    ecart_max = max(ecarts)
    p99 = sorted(ecarts)[math.ceil(0.99 * len(ecarts)) - 1]
    print(f"\n== 1. Équivalence ancien / nouveau : {len(ecarts)} couples (instant, course, gare)")
    print(f"   écart ≤ {SEUIL_ECART_99_S:g} s : {part_3s * 100:.2f} % (seuil : au moins 99 %)")
    print(f"   écart au 99e centile : {p99:.1f} s ; maximum : {ecart_max:.1f} s (seuil : au plus {SEUIL_ECART_MAX_S:g} s)")
    print(f"   couples non chiffrés d'un seul côté : {sum(1 for e in ecarts if e == math.inf)}")
    ok_equivalence = part_3s >= 0.99 and ecart_max <= SEUIL_ECART_MAX_S

    # 2. Palaiseau : sauts et erreur de départ, fenêtre des relevés réels (mêmes mesures que le 18/11)
    resultats = {}
    for nom, trace in (("ancien", trace_a), ("nouveau", trace_n)):
        f = fenetre(trace, debut_fenetre, fin_fenetre)
        s = sauts(f)
        sj = sejours(f, nom_fn(PALAISEAU), courses, verite)
        deps = [abs(x[3]) for x in sj if x[3] is not None]
        resultats[nom] = (len(s), statistics.median(deps) if deps else math.inf, s)
    print("\n== 2. Palaiseau (fenêtre des relevés réels)")
    for nom in ("ancien", "nouveau"):
        n_sauts, dep, _ = resultats[nom]
        print(f"   {nom:8s} : sauts de plus de 110 m en {PAS_S} s : {n_sauts} ; erreur médiane du départ affiché : {dep:.0f} s")
    n_sauts, dep_med, liste = resultats["nouveau"]
    for course, t, m, _n, e0, e1, g0, g1 in sorted(liste, key=lambda x: -x[2])[:6]:
        print(f"      saut nouveau {t:%H:%M:%S} {course[-12:]} {m:.0f} m : {e0} {g0} -> {e1} {g1}")
    ok_sauts = n_sauts <= SEUIL_SAUTS
    ok_depart = dep_med <= SEUIL_DEPART_MEDIAN_S

    # 3. Mesures sans seuil : taille de l'état, pendant le rejeu du nouveau comportement
    print("\n== 3. Taille de l'état (nouveau comportement, mesurée à chaque relevé reçu)")
    if tailles:
        max_l = max(tailles, key=lambda x: x[1])
        max_m = max(tailles, key=lambda x: x[3])
        fin_t = tailles[-1]
        print(f"   relevés mesurés : {len(tailles)} ; courses dans le lisseur (fin) : {fin_t[5]} ; dans la mémoire (fin) : {fin_t[6]}")
        print(f"   Lisseur.exporter() : fin {fin_t[1] / 1024:.1f} Ko (gzip {fin_t[2] / 1024:.1f} Ko) ; "
              f"maximum {max_l[1] / 1024:.1f} Ko (gzip {max_l[2] / 1024:.1f} Ko)")
        print(f"   Memoire.exporter() : fin {fin_t[3] / 1024:.1f} Ko (gzip {fin_t[4] / 1024:.1f} Ko) ; "
              f"maximum {max_m[3] / 1024:.1f} Ko (gzip {max_m[4] / 1024:.1f} Ko)")

    print("\n== Verdict (seuils du ticket, non ajustés)")
    print(f"   équivalence : {'ATTEINTE' if ok_equivalence else 'NON ATTEINTE'}")
    print(f"   sauts ≤ {SEUIL_SAUTS} : {'ATTEINT' if ok_sauts else 'NON ATTEINT'} ({n_sauts})")
    print(f"   erreur médiane de départ ≤ {SEUIL_DEPART_MEDIAN_S:g} s : {'ATTEINT' if ok_depart else 'NON ATTEINT'} ({dep_med:.0f} s)")
    tout = ok_equivalence and ok_sauts and ok_depart
    print(f"   => {'TOUS LES SEUILS ATTEINTS' if tout else 'ARRÊT : au moins un seuil non atteint, aucun repli implémenté'}")
    return 0 if tout else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else Path.home() / "rerb-data-palaiseau-20261118"))
