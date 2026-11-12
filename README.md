# rerb-live

Où est physiquement le RER B que j'attends ? Une carte qui estime la position des trains à partir des données temps réel PRIM (Île-de-France Mobilités). Premier arrêt : Massy - Palaiseau.

**État : investigation** (12/11/2026). Aucun code encore. Voir `docs/bilan-investigation-2026-11-12.md` et `docs/taches-demain.md`.

## Principe visé
Position **estimée** (pas GPS) : horaires estimés à chaque arrêt (PRIM `estimated-timetable`) + tracé de la ligne (open data IDFM) → interpolation entre deux gares.

## Avertissements
- Quota d'un jeton neuf : 1 000 requêtes par jour par API (mesuré le 12/11/2026).
- Licence des données PRIM ambiguë (Licence Mobilités / ODbL) : à clarifier avant toute publication d'historique.
- Le jeton PRIM va dans `.env` (ignoré par git), jamais dans le dépôt.
