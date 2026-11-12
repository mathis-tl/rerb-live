# rerb-live

Où est physiquement le RER B que j'attends ? Une carte qui estime la position des trains à partir des données temps réel PRIM (Île-de-France Mobilités). Premier arrêt : Massy - Palaiseau.

**État (12/11/2026) :** investigation terminée, enregistreur de données écrit (`recorder/`), collecte de 24 h en cours sur Boston, Helsinki et le RER B. L'estimation de position n'est pas encore écrite. Voir `docs/plan-rerb-b.md`, `docs/donnees-necessaires.md` et `docs/pendant-la-collecte.md`.

## Principe visé
Position **estimée** (pas GPS) : horaires estimés à chaque arrêt (PRIM `estimated-timetable`) + tracé de la ligne (open data IDFM) → interpolation entre deux gares. La qualité de cette estimation se mesure sur des réseaux qui publient aussi le GPS (Boston, Helsinki), d'où la collecte en cours.

## Avertissements
- Quotas PRIM par API (en-têtes du 12/11/2026) : `estimated-timetable` 1 500 requêtes par jour, `stop-monitoring` 1 000 000 par jour.
- Licence des données PRIM ambiguë (Licence Mobilités / ODbL) : à clarifier avant toute publication d'historique. Aucune donnée brute ne se publie (licences ODbL, MassDOT, HSL).
- Les clés (`PRIM_API_KEY`, `MBTA_API_KEY`) vont dans `.env` (ignoré par git), jamais dans le dépôt. Modèle : `.env.example`.
