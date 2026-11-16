# rerb-live

Où est physiquement le RER B que j'attends ? Une carte qui estime la position des trains à partir des données temps réel PRIM (Île-de-France Mobilités). Premier arrêt : Massy - Palaiseau.

**État (17/11/2026) :** investigation, collecte 1 (24 h) et collecte 2 (47 gares) terminées. **Tâche 4 en cours** : J1 à J4 faits (lat/lon sur un tronçon ; vérité RER B à Massy puis sur 47 gares, état « à quai » estimé inquiétant, départ à ±1 min mais en avance de 53 s ; serveur local ; carte 3D deck.gl avec décor OpenStreetMap de toute la ligne, panneau « prochains trains à une gare », limite de vitesse à 33 m/s). Lancer sans PRIM : `python3 rerb/serveur.py --demo` puis http://127.0.0.1:8765/ ; avec PRIM : sans `--demo` (clé dans `.env`). **Problème connu :** PRIM annonce des horaires impossibles sur quelques tronçons (2 % des trajets, ex. Massy - Palaiseau → Palaiseau en 30 s pour 1 435 m) ; `stop-monitoring` donne les mêmes heures (test du 16/11, `docs/plan-tache-4.md` §20). **Prochaine étape :** durée minimale plausible par tronçon, mesurée sur les relevés déjà enregistrés (§21). Estimation de position niveau 1 : `rerb/position.py` avec mémoire des gares retirées (`rerb/memoire.py`) ; erreur mesurée sur Boston et Helsinki (13/11, un seul jour) : médiane 350 m et 276 m, valable pour la méthode, pas pour le RER B (`docs/recap-mesure-boston-helsinki.md`). Source de vérité du plan : `docs/plan-tache-4.md` ; reprise de session : `docs/prompt-de-reprise.md`.

## Principe visé
Position **estimée** (pas GPS) : horaires estimés à chaque arrêt (PRIM `estimated-timetable`) + tracé de la ligne (open data IDFM) → interpolation entre deux gares. La qualité de cette estimation se mesure sur des réseaux qui publient aussi le GPS (Boston, Helsinki), d'où la collecte de 24 h déjà faite. Pour le RER B, la vérité vient de `VehicleAtStop` (train à quai) de `stop-monitoring`.

## Avertissements
- Quotas PRIM par API (en-têtes du 12/11/2026) : `estimated-timetable` 1 500 requêtes par jour, `stop-monitoring` 1 000 000 par jour.
- Licence des données PRIM ambiguë (Licence Mobilités / ODbL) : à clarifier avant toute publication d'historique. Aucune donnée brute ne se publie (licences ODbL, MassDOT, HSL).
- Les clés (`PRIM_API_KEY`, `MBTA_API_KEY`) vont dans `.env` (ignoré par git), jamais dans le dépôt. Modèle : `.env.example`.
