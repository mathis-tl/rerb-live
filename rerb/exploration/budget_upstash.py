"""Budget Upstash de T5 : taille de l'état stocké (rejeu des relevés réels de Palaiseau) et commandes par jour / par mois.

Exploration, pas un test. Lit `~/rerb-data-palaiseau-20261118` (hors dépôt). N'appelle ni PRIM ni Upstash.
Hypothèses, affichées ci-dessous et à confirmer en production :
• trafic continu 24 h, une copie active lit le magasin toutes les CACHE_LOCAL_S (cache local du relayeur) ;
• un rafraîchissement PRIM par INTERVALLE_S hors coupure de nuit, partagé entre les copies (verrou) ;
• /gare : une lecture du blocage à chaque échec du cache CDN (s-maxage 60 s), une URL par gare, une seule zone de cache.
Usage (depuis rerb/) : python3 exploration/budget_upstash.py
"""
import sys
from datetime import timedelta
from pathlib import Path

RERB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RERB))
sys.path.insert(0, str(RERB / "exploration"))

from lissage import Lisseur  # noqa: E402
from magasin import compresser_json  # noqa: E402
from memoire import TAILLE_MAX_OCTETS, Memoire  # noqa: E402
from relayeur import CACHE_LOCAL_S, CLE_LISSEUR, CLE_MEMOIRE, CLE_RELEVE  # noqa: E402
from serveur import INTERVALLE_S, Releve, consolider_livraison  # noqa: E402
from test_relayeur import DONNEES_PALAISEAU, _decaler  # noqa: E402

import rejouer_palaiseau  # noqa: E402

CYCLES = 10                       # 2 h réelles répétées : ~20 h, comme le test de purge
JOURS_PAR_MOIS = 30
LIMITE_COMMANDES = 500_000        # Upstash, par mois
LIMITE_OCTETS = 10 * 10**9        # 10 Go par mois
SEUIL_ALERTE = 0.6                # alerte à 60 % de chaque limite (décision T5)
NB_GARES = 47                     # liste blanche de /gare
MINUTES_PAR_JOUR = 24 * 60
MINUTES_UTILES = MINUTES_PAR_JOUR - 4 * 60   # coupure de nuit 01:00-05:00 : aucun rafraîchissement
# Commandes en plus de la lecture du cache local, par rafraîchissement : SET NX, MGET sous verrou, GET compteur, INCR, MSET, EVAL.
COMMANDES_PAR_RAFRAICHISSEMENT = 6
OCTETS_PAR_RAFRAICHISSEMENT = 2   # MGET sous verrou + MSET de l'état (lectures/écritures comptées en octets, approximation)


def mesurer(sequence):
    """sequence : [(instant, livraison)] dans l'ordre. Rejoue le relayeur (consolidation, purge, lissage) et mesure les valeurs
    qu'il écrirait dans Upstash : taille gzip+base64 de chaque clé, et taille brute de la mémoire (garde de TAILLE_MAX_OCTETS)."""
    memoire, lisseur = Memoire(), Lisseur()
    pire = {"total": 0, CLE_RELEVE: 0, CLE_LISSEUR: 0, CLE_MEMOIRE: 0}
    brute_max, finale = 0, None
    for instant, livraison in sequence:
        trains = consolider_livraison(livraison, memoire)
        memoire.purger(instant)
        lisseur.avancer({ref: train for ref, (train, _retards) in trains.items()}, instant)
        valeurs = {
            CLE_RELEVE: len(compresser_json(Releve(instant, trains).exporter())),
            CLE_LISSEUR: len(compresser_json(lisseur.exporter())),
            CLE_MEMOIRE: len(compresser_json(memoire.exporter())),
        }
        valeurs["total"] = sum(valeurs.values())
        for cle, octets in valeurs.items():
            pire[cle] = max(pire[cle], octets)
        brute_max = max(brute_max, memoire.taille_export())
        finale = valeurs
    return pire, finale, brute_max


def budget(visiteurs, copies):
    """Commandes Upstash par mois pour `visiteurs` (visiteurs simultanés) et `copies` copies de fonction actives toute la journée."""
    lectures_trains = (86400 // CACHE_LOCAL_S) * copies                  # une lecture par copie et par CACHE_LOCAL_S
    rafraichissements = MINUTES_UTILES * 60 // INTERVALLE_S              # un par INTERVALLE_S, partagé
    commandes_trains = lectures_trains + rafraichissements * COMMANDES_PAR_RAFRAICHISSEMENT
    gares_distinctes = min(visiteurs, NB_GARES)                          # pire cas : chaque visiteur sur une gare différente
    lectures_gare = gares_distinctes * MINUTES_PAR_JOUR                  # une lecture du blocage par échec du cache CDN (60 s)
    return {
        "trains_jour": commandes_trains,
        "gare_jour": lectures_gare,
        "mois": (commandes_trains + lectures_gare) * JOURS_PAR_MOIS,
        "rafraichissements_jour": rafraichissements,
        "lectures_trains_jour": lectures_trains,
    }


def main():
    if not (DONNEES_PALAISEAU / "prim_estimated_timetable").is_dir():
        print(f"Données absentes : {DONNEES_PALAISEAU}")
        return 1
    releves = rejouer_palaiseau.lire_releves(DONNEES_PALAISEAU)
    duree = releves[-1][0] - releves[0][0] + timedelta(minutes=1)
    print(f"Rejeu : {len(releves)} relevés réels, {duree} par cycle, {CYCLES} cycles")

    pire_reel, finale_reel, brute_reel = mesurer(releves)
    sequence = [(instant + duree * c, _decaler(sd, f"#{c}", duree * c)) for c in range(CYCLES) for instant, sd in releves]
    pire, finale, brute = mesurer(sequence)

    print("\nTaille stockée (gzip + base64, octets), pire cas :")
    print(f"  2 h réelles   : total {pire_reel['total']}  releve {pire_reel[CLE_RELEVE]}  lisseur {pire_reel[CLE_LISSEUR]}  "
          f"memoire {pire_reel[CLE_MEMOIRE]}  (mémoire brute max {brute_reel} / garde {TAILLE_MAX_OCTETS})")
    print(f"  ~20 h rejouées: total {pire['total']}  releve {pire[CLE_RELEVE]}  lisseur {pire[CLE_LISSEUR]}  "
          f"memoire {pire[CLE_MEMOIRE]}  (mémoire brute max {brute} / garde {TAILLE_MAX_OCTETS})")
    print(f"  finale ~20 h : total {finale['total']}")

    print("\nBudget Upstash (hypothèses : trafic continu 24 h, une zone de cache CDN, copies actives = N) :")
    print(f"  {'visiteurs':>9} {'copies':>6} {'/trains par mois':>18} {'/gare par mois':>16} {'total par mois':>16} {'% de 500 000':>13}")
    for visiteurs, copies in ((5, 1), (50, 2), (500, 5)):
        b = budget(visiteurs, copies)
        gare_mois = b["gare_jour"] * JOURS_PAR_MOIS
        trains_mois = b["trains_jour"] * JOURS_PAR_MOIS
        total = b["mois"]
        print(f"  {visiteurs:>9} {copies:>6} {trains_mois:>18,} {gare_mois:>16,} {total:>16,} {100 * total / LIMITE_COMMANDES:>12.0f} %")

    octets_jour = (budget(5, 1)["lectures_trains_jour"] + OCTETS_PAR_RAFRAICHISSEMENT * budget(5, 1)["rafraichissements_jour"]) * pire["total"]
    print(f"\nTransfert /trains (1 copie) : ~{octets_jour * JOURS_PAR_MOIS / 10**9:.2f} Go par mois "
          f"contre {LIMITE_OCTETS / 10**9:.0f} Go (alerte à {SEUIL_ALERTE:.0%}). Alerte commandes : {SEUIL_ALERTE * LIMITE_COMMANDES:,.0f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
