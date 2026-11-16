# rerb-live

Où est physiquement le RER B que j'attends ? Une carte qui estime la position des trains à partir des données temps réel PRIM (Île-de-France Mobilités). Premier arrêt : Massy - Palaiseau.

**État (15/11/2026) :** investigation et collecte de 24 h terminées (Boston, Helsinki, RER B). **Tâche 4 en cours** : J1 (latitude et longitude sur un tronçon), J2 (vérité RER B à Massy : état « à quai » estimé inquiétant, rappel 37,6 % et précision 41,0 %, départ estimé en avance de 80 s) et J2 bis (même mesure sur 47 gares, collecte 2 terminée le 16/11 : rappel 33,0 %, précision 57,0 %, inquiétants ; départ médiane 44 s, biais +53 s) faits ; J3 (serveur local) fait ; J4 (carte 3D, rame provisoire) en cours : `python3 rerb/serveur.py --demo` puis http://127.0.0.1:8765/ (trains inventés, aucun appel PRIM). Détail : `docs/recap-j2bis-verite-gares.md`, `docs/plan-tache-4.md` §0.14. Détail : `docs/plan-tache-4.md`. Estimation de position niveau 1 écrite (`rerb/position.py`) avec mémoire des gares retirées par PRIM (`rerb/memoire.py`). Erreur mesurée sur Boston et Helsinki (13/11, un seul jour) : médiane de l'erreur en route 350 m et 276 m, contre 1 999 m et 1 049 m (médiane) pour le meilleur repère trivial ; 83,0 % des positions mesurées à Boston, 68,6 % à Helsinki (trains distincts), le reste écarté et compté. Valable pour la méthode, pas une preuve pour le RER B. Voir `docs/recap-mesure-boston-helsinki.md`, `docs/plan-rerb-b.md` et `docs/cadrage-tache-3.md`.

## Principe visé
Position **estimée** (pas GPS) : horaires estimés à chaque arrêt (PRIM `estimated-timetable`) + tracé de la ligne (open data IDFM) → interpolation entre deux gares. La qualité de cette estimation se mesure sur des réseaux qui publient aussi le GPS (Boston, Helsinki), d'où la collecte de 24 h déjà faite. Pour le RER B, la vérité vient de `VehicleAtStop` (train à quai) de `stop-monitoring`.

## Avertissements
- Quotas PRIM par API (en-têtes du 12/11/2026) : `estimated-timetable` 1 500 requêtes par jour, `stop-monitoring` 1 000 000 par jour.
- Licence des données PRIM ambiguë (Licence Mobilités / ODbL) : à clarifier avant toute publication d'historique. Aucune donnée brute ne se publie (licences ODbL, MassDOT, HSL).
- Les clés (`PRIM_API_KEY`, `MBTA_API_KEY`) vont dans `.env` (ignoré par git), jamais dans le dépôt. Modèle : `.env.example`.
