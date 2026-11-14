# rerb-live

Où est physiquement le RER B que j'attends ? Une carte qui estime la position des trains à partir des données temps réel PRIM (Île-de-France Mobilités). Premier arrêt : Massy - Palaiseau.

**État (14/11/2026) :** investigation et collecte de 24 h terminées (Boston, Helsinki, RER B). Estimation de position niveau 1 écrite (`rerb/position.py`) avec mémoire des gares retirées par PRIM (`rerb/memoire.py`). Erreur mesurée sur Boston et Helsinki (13/11, un seul jour) : médiane de l'erreur en route 350 m et 276 m, contre 1 999 m et 1 049 m (médiane) pour le meilleur repère trivial ; 83,0 % des positions mesurées à Boston, 68,6 % à Helsinki (trains distincts), le reste écarté et compté. Valable pour la méthode, pas une preuve pour le RER B. Voir `docs/recap-mesure-boston-helsinki.md`, `docs/plan-rerb-b.md` et `docs/cadrage-tache-3.md`.

## Principe visé
Position **estimée** (pas GPS) : horaires estimés à chaque arrêt (PRIM `estimated-timetable`) + tracé de la ligne (open data IDFM) → interpolation entre deux gares. La qualité de cette estimation se mesure sur des réseaux qui publient aussi le GPS (Boston, Helsinki), d'où la collecte en cours.

## Avertissements
- Quotas PRIM par API (en-têtes du 12/11/2026) : `estimated-timetable` 1 500 requêtes par jour, `stop-monitoring` 1 000 000 par jour.
- Licence des données PRIM ambiguë (Licence Mobilités / ODbL) : à clarifier avant toute publication d'historique. Aucune donnée brute ne se publie (licences ODbL, MassDOT, HSL).
- Les clés (`PRIM_API_KEY`, `MBTA_API_KEY`) vont dans `.env` (ignoré par git), jamais dans le dépôt. Modèle : `.env.example`.
