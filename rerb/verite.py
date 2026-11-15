"""Tâche 4, J2 : vérité RER B à Massy - Palaiseau. `VehicleAtStop` (stop-monitoring) contre `estimer`.

Plan : docs/plan-tache-4.md (J2, arrêt S2). Seuils fixés avant de regarder les résultats (SEUILS).
• classer_a_quai   : à un relevé, vrais « à quai » contre estimés « à quai » -> comptes exclusifs.
• departs_observes : suite de relevés -> (train, dernier relevé à quai, premier relevé sans lui).
• ecart_depart     : écart entre milieu de l'intervalle observé et heure estimée (positif = estimation en avance, comme E1).
• rapport / verdict: taux avec comptes bruts, lecture contre les seuils.
Fonctions pures. Limite : `VehicleAtStop` et les heures estimées viennent du même système SNCF, donc pas indépendants.
"""
from datetime import timedelta

ECART_MAX_RELEVES = timedelta(seconds=150)  # au-delà, un relevé manque : départ inobservable

# (bon à partir de, inquiétant en dessous de) ; pour les durées : (bon jusqu'à, sans objet)
SEUILS = {"rappel": (0.80, 0.60), "précision": (0.80, 0.60),
          "médiane départ": (120.0, None), "p90 départ": (300.0, None)}

VP, MANQUE_ETAT, MANQUE_ABSENT = "vrai positif", "manqué : autre état estimé", "manqué : train absent des prévisions"
FP, FP_ABSENT = "faux positif", "estimé à quai, absent de stop-monitoring"


def classer_a_quai(vrais, visibles, connus, predits):
    """vrais : courses avec VehicleAtStop vrai ; visibles : listées par stop-monitoring ; connus : présentes dans le
    dernier relevé estimated-timetable ; predits : estimées « à quai à Massy » par `estimer`."""
    return {
        VP: len(vrais & predits),
        MANQUE_ETAT: len((vrais & connus) - predits),
        MANQUE_ABSENT: len(vrais - connus),
        FP: len((predits & visibles) - vrais),
        FP_ABSENT: len(predits - visibles),
    }


def departs_observes(releves):
    """releves : [(instant, ensemble des courses à quai)] en ordre croissant.
    -> [(course, dernier relevé à quai, premier relevé suivant sans elle)] ; trou de relevés trop long : ignoré."""
    sorties = []
    for (t1, avant), (t2, apres) in zip(releves, releves[1:]):
        if t2 - t1 > ECART_MAX_RELEVES:
            continue
        sorties += [(c, t1, t2) for c in sorted(avant - apres)]
    return sorties


def ecart_depart(estime, bas, haut):
    """-> (écart en secondes, dans l'incertitude). Départ observé quelque part entre `bas` et `haut` : on prend le
    milieu. Écart = milieu moins estimé (positif = estimation en AVANCE sur le train réel, convention E1)."""
    milieu = bas + (haut - bas) / 2
    ecart = (milieu - estime).total_seconds()
    return ecart, abs(ecart) <= (haut - bas).total_seconds() / 2


def rapport(c):
    vrais = c[VP] + c[MANQUE_ETAT] + c[MANQUE_ABSENT]
    return {
        "rappel": (c[VP], vrais),
        "précision": (c[VP], c[VP] + c[FP]),
        "précision, absents inclus": (c[VP], c[VP] + c[FP] + c[FP_ABSENT]),
    }


def verdict(nom, valeur):
    bon, inquietant = SEUILS[nom]
    if inquietant is None:  # durée : plus petit est mieux
        return "bon" if valeur <= bon else "pas bon"
    if valeur >= bon:
        return "bon"
    return "moyen" if valeur >= inquietant else "inquiétant"
