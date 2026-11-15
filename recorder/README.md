# Enregistreur brut (test 1)

Enregistre sans analyse : MBTA (Boston), HSL (Helsinki), PRIM (RER B). Contexte et choix : `docs/donnees-necessaires.md`. L'explication détaillée de « qui fait quoi » est en tête de `recorder.py`.

## Installation (une fois)

```
cd recorder
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Les clés (`MBTA_API_KEY`, `PRIM_API_KEY`) se lisent dans un `.env` (option `--env` ou variable `RERB_ENV`, jamais versionné).

## Lancer

Test court (3 minutes, dossier séparé) :

```
caffeinate -i .venv/bin/python recorder.py --out ~/rerb-data-test --duration 190
```

Test 1 de 24 h (Mac branché, capot ouvert) :

```
caffeinate -i .venv/bin/python recorder.py --duration 86400
```

Arrêt propre : Ctrl-C (SIGINT), SIGTERM ou fermeture du terminal (SIGHUP). Le programme ne se relance pas seul.

**Un seul enregistreur à la fois par dossier** (verrou `.lock`), et un seul enregistreur utilisant PRIM à la fois sur la machine (verrou `~/.rerb-prim-quota.json.lock`). **Quota PRIM :** le compteur d'appels du jour est dans `~/.rerb-prim-quota.json`, partagé entre tous les dossiers, plafond 1 450 appels par jour UTC. Les appels faits à la main en dehors de l'enregistreur ne sont pas comptés : garder de la marge. Pour un test court pendant que le 24 h tourne, ajouter `--sources mbta,hsl` (sans PRIM).

**2e collecte (15/11/2026), multi-gares :** source `primgares` = `stop-monitoring` sur les 47 gares du RER B (liste lue dans `rerb/donnees/arrets_rerb_C01743.csv`), un cycle toutes les 30 s (`--gares-interval`, minimum 20 s), corps brut gardé, flux `prim_stop_monitoring_gares`. Quota séparé : compteur `~/.rerb-prim-gares-quota.json`, plafond 300 000 par jour UTC (`--gares-daily-cap`) pour 135 360 prévus. Un 401, 403 ou 429 arrête le cycle en cours. Commande lancée : `caffeinate -i .venv/bin/python recorder.py --sources prim,primgares --out ~/rerb-data-2 --duration <secondes jusqu'à 17:00>`. Le verrou PRIM est commun à `prim` et `primgares`.

**Code de sortie :** 0 si arrêt normal, 1 si un fil est mort ou si le disque est presque plein.

## Sortie

Un fichier `.jsonl.gz` par flux et par heure UTC de réception : `<out>/<flux>/<AAAA-MM-JJ>/<flux>_<AAAA-MM-JJTHH>.jsonl.gz`. Lecture : `gzip -dc fichier | head -1`. Chaque ligne JSON contient l'heure de réception (UTC et locale), l'URL sans clé, le code HTTP, les en-têtes utiles, la durée, l'erreur éventuelle et `body_b64` (corps de la réponse en base64). Le flux MQTT contient une ligne par message (`payload` en texte, heure de réception en UTC seulement).

Flux « témoin » : `hsl_witness_trip_updates` contient, une fois par heure, le corps non filtré du même appel que celui filtré dans `hsl_trip_updates`. `hsl_witness_vehicle_positions` contient une fois par heure les positions HSL de tous les modes.

`recorder.log` : démarrages, erreurs, coupures, veilles du Mac (« SAUT D'HORLOGE OU VEILLE »), « THREAD MORT » si un fil s'arrête, et une ligne de compteurs **cumulés** chaque minute.

## Tests

```
.venv/bin/python -m unittest test_recorder
```
