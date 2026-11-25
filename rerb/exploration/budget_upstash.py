"""Budget Upstash (T5b) : simulation de journées actives, relevés réels de Palaiseau rejoués.

Exploration, pas un test. Lit `~/rerb-data-palaiseau-20261118` (hors dépôt). Aucun réseau : FauxUpstash compte les commandes et les
octets (envoyés + reçus) ; PRIM est rejoué (le relevé suivant, décalé à l'instant de l'appel) ; horloge simulée.

Scénarios (décision de Mathis du 25/11 : base = 10 visiteurs au maximum par jour, pas de promotion) :
• BASE : 10 visiteurs × 30 min, bout à bout = 5 h actives par jour, 1 et 3 copies. C'est le critère d'acceptation de T5b.
• TÉMOIN : 50 visiteurs (ancienne base du ticket), 20 h actives par jour, 3 copies. Gardé pour traçabilité ; non atteint.

Hypothèses communes :
• une requête au relayeur toutes les 5 s pendant la fenêtre active (pire cas du CDN, s-maxage=5), en tour de rôle entre les copies ;
  c'est un plafond en trafic continu : une actualisation n'a lieu que si quelqu'un demande la page ;
• pas de coupure de nuit (fenetre_nuit None) ;
• /gare ne lit pas Upstash : aucune commande ajoutée par les visiteurs de /gare ;
• un mois = 30 jours, chaque jour identique.

Usage (depuis rerb/) : python3 exploration/budget_upstash.py   (plusieurs minutes)
"""
import functools
import sys
from datetime import timedelta
from pathlib import Path

RERB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RERB))
sys.path.insert(0, str(RERB / "exploration"))

from magasin_upstash import MagasinUpstash  # noqa: E402
from relayeur import Relayeur  # noqa: E402
from test_faux_upstash import JETON_FICTIF, FauxUpstash  # noqa: E402
from test_relayeur import DONNEES_PALAISEAU, URL_FICTIVE, _decaler  # noqa: E402
from test_serveur import T0  # noqa: E402

import rejouer_palaiseau  # noqa: E402

PAS_REQUETE_S = 5
JOURS_PAR_MOIS = 30
LIMITE_COMMANDES = 500_000
LIMITE_OCTETS = 10 * 10**9

# Scénario de base (critère de T5b, décidé le 25/11)
VISITEURS_BASE = 10
DUREE_VISITE_MIN = 30
HEURES_BASE = VISITEURS_BASE * DUREE_VISITE_MIN / 60   # 5 h, visites bout à bout (pire cas de répartition)
COPIES_BASE = (1, 3)
CIBLE_COMMANDES_MOIS = 250_000        # ticket T5b : 50 % de 500 000 commandes par mois
CIBLE_OCTETS_MOIS = 6 * 10**9         # ticket T5b : 60 % de 10 Go de transfert par mois

# Témoin (ancienne base du ticket, non retenue)
VISITEURS_TEMOIN, HEURES_TEMOIN, COPIES_TEMOIN = 50, 20, 3


@functools.lru_cache(maxsize=None)
def _rejeux():
    """Relevés réels (instant de réception UTC, livraison PRIM), lus une seule fois."""
    return rejouer_palaiseau.lire_releves(DONNEES_PALAISEAU)


def simuler(copies, heures, pas_s=PAS_REQUETE_S):
    """Une journée de `heures` heures actives avec `copies` copies du relayeur sur un magasin partagé.
    -> {"commandes", "octets" (envoyés + reçus), "appels_prim", "requetes", "tailles" (octets stockés par clé, en fin de journée)}."""
    releves = _rejeux()
    t = {"s": 0.0}   # horloge simulée : secondes depuis T0
    faux = FauxUpstash(lambda: t["s"])
    magasin = MagasinUpstash(URL_FICTIVE, JETON_FICTIF, http=faux.http)
    appels = {"n": 0}

    def prim():
        """Le relevé suivant, horodatages décalés à l'instant de l'appel (les courses restent distinctes à chaque appel)."""
        maintenant = T0 + timedelta(seconds=t["s"])
        n = appels["n"]
        appels["n"] += 1
        instant, livraison = releves[n % len(releves)]
        return _decaler(livraison, f"#{n}", maintenant - instant)

    relayeurs = [Relayeur(magasin, prim, fenetre_nuit=None) for _ in range(copies)]
    requetes = 0
    for i, s in enumerate(range(0, int(heures * 3600), pas_s)):
        t["s"] = float(s)
        relayeurs[i % copies].lire(T0 + timedelta(seconds=s))
        requetes += 1
    return {
        "commandes": len(faux.commandes),
        "octets": faux.octets_envoyes + faux.octets_recus,
        "appels_prim": appels["n"],
        "requetes": requetes,
        "tailles": {cle: len(texte) for cle, (texte, _expiration) in faux.valeurs.items()},
    }


def par_mois(journee):
    return journee["commandes"] * JOURS_PAR_MOIS, journee["octets"] * JOURS_PAR_MOIS


def _ligne(libelle, j):
    cmd, octets = par_mois(j)
    return (f"{libelle:>22} {j['appels_prim']:>10,} {j['commandes']:>12,} {cmd:>14,} {100 * cmd / LIMITE_COMMANDES:>11.0f} % "
            f"{octets / 10**9:>9.2f} {100 * octets / LIMITE_OCTETS:>10.0f} %")


def main():
    if not (DONNEES_PALAISEAU / "prim_estimated_timetable").is_dir():
        print(f"Données absentes : {DONNEES_PALAISEAU}")
        return 1
    print(f"Rejeu : {len(_rejeux())} relevés réels ; une requête toutes les {PAS_REQUETE_S} s pendant la fenêtre active.\n")
    print(f"{'scénario':>22} {'PRIM/jour':>10} {'commandes/j':>12} {'commandes/mois':>14} {'% de 500 000':>12} "
          f"{'Go/mois':>9} {'% de 10 Go':>11}")

    base = {c: simuler(c, HEURES_BASE) for c in COPIES_BASE}
    for c, j in base.items():
        print(_ligne(f"base {VISITEURS_BASE} v, {c} copie(s)", j))
    temoin = simuler(COPIES_TEMOIN, HEURES_TEMOIN)
    print(_ligne(f"témoin {VISITEURS_TEMOIN} v, {COPIES_TEMOIN} copies", temoin))

    print(f"\nCritère T5b (base : {VISITEURS_BASE} visiteurs × {DUREE_VISITE_MIN} min = {HEURES_BASE:g} h, {COPIES_BASE[-1]} copies) :")
    cmd, octets = par_mois(base[COPIES_BASE[-1]])
    print(f"  commandes/mois : {cmd:,} (cible ≤ {CIBLE_COMMANDES_MOIS:,}) -> {'atteinte' if cmd <= CIBLE_COMMANDES_MOIS else 'NON atteinte'}")
    print(f"  transfert/mois : {octets / 10**9:.2f} Go (cible ≤ {CIBLE_OCTETS_MOIS / 10**9:.0f} Go) -> "
          f"{'atteinte' if octets <= CIBLE_OCTETS_MOIS else 'NON atteinte'}")

    j = base[COPIES_BASE[-1]]
    print(f"\nTaille stockée par clé à la fin de la journée (base, {COPIES_BASE[-1]} copies, octets de la valeur JSON) :")
    for cle, octets_cle in sorted(j["tailles"].items(), key=lambda x: -x[1]):
        print(f"  {cle:<28} {octets_cle:>10,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
