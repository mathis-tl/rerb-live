"""Équivalence de /trains (T5b) : réponses `reponse_trains` sur les relevés réels de Palaiseau, une requête toutes les 5 s.

Exploration, pas un test. Lit `~/rerb-data-palaiseau-20261118` (hors dépôt). Aucun réseau : faux Upstash (test_faux_upstash) et
PRIM rejoué (le relevé suivant est servi quand le relayeur appelle PRIM). Écrit toutes les réponses dans un fichier JSON pour
comparer deux versions du code : `python3 exploration/rejeu_equivalence_trains.py <sortie.json>`.
Affiche aussi le nombre de commandes Upstash envoyées au total (lectures et écritures).
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

RERB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RERB))
sys.path.insert(0, str(RERB / "exploration"))

import rejouer_palaiseau  # noqa: E402
from prod import Services, reponse_trains  # noqa: E402
from test_relayeur import DONNEES_PALAISEAU  # noqa: E402
from test_faux_upstash import JETON_FICTIF, FauxUpstash  # noqa: E402

PAS = timedelta(seconds=5)
ENV = {"PRIM_API_KEY": "cle-fictive", "UPSTASH_REDIS_REST_URL": "https://magasin.fictif.invalid", "UPSTASH_REDIS_REST_TOKEN": JETON_FICTIF}


def rejouer(releves):
    """-> (liste de {t, corps}, nombre de commandes Upstash)."""
    maintenant = {"t": releves[0][0]}
    prim = {"livraison": None}
    faux = FauxUpstash(lambda: maintenant["t"].timestamp())

    def appeler(cle, url, delai=None):
        return prim["livraison"]

    svc = Services(ENV, http=faux.http, appeler=appeler, horloge=lambda: maintenant["t"])
    sortie = []
    index = 0
    instant = releves[0][0]
    fin = releves[-1][0] + timedelta(seconds=90)
    while instant <= fin:
        # le relevé « reçu » à cet instant est le dernier dont l'heure de réception est passée
        while index + 1 < len(releves) and releves[index + 1][0] <= instant:
            index += 1
        prim["livraison"] = releves[index][1]
        maintenant["t"] = instant
        sortie.append({"t": instant.isoformat(), "corps": reponse_trains(svc)})
        instant += PAS
    return sortie, len(faux.commandes)


def main(sortie_json):
    releves = rejouer_palaiseau.lire_releves(DONNEES_PALAISEAU)
    reponses, commandes = rejouer(releves)
    Path(sortie_json).write_text(json.dumps(reponses, sort_keys=True, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(releves)} relevés, {len(reponses)} requêtes /trains, {commandes} commandes Upstash, sortie : {sortie_json}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage : python3 exploration/rejeu_equivalence_trains.py <sortie.json>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
