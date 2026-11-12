"""Tâche 2 : relier une gare de PRIM (StopArea) à ses coordonnées.

Qui fait quoi :
• stop_area_number : extrait le numéro de "STIF:StopArea:SP:<n>:".
• load_stops       : lit le fichier statique des gares (CSV IDFM) et indexe par numéro.
• lookup           : identifiant PRIM -> Stop (nom, lat, lon), ou erreur claire.
• stop_area_refs   : liste les gares distinctes d'une réponse estimated-timetable.

Le numéro de "STIF:StopArea:SP:<n>:" est le même que celui de
"IDFM:monomodalStopPlace:<n>" (vérifié sur 47 gares sur 47, 12/11/2026).
Les quais (STIF:StopPoint:Q:...) sont volontairement refusés : niveau gare seulement.
"""
import csv
import re
from dataclasses import dataclass
from pathlib import Path

_STOP_AREA = re.compile(r"^STIF:StopArea:SP:(\d+):$")
_STATIC = re.compile(r"^IDFM:monomodalStopPlace:(\d+)$")


class StopNotFound(ValueError):
    """Identifiant illisible, non-gare, ou absent du fichier statique."""


@dataclass(frozen=True)
class Stop:
    number: str
    name: str
    lat: float
    lon: float


def stop_area_number(ref):
    """'STIF:StopArea:SP:58774:' -> '58774'. Lève StopNotFound sinon."""
    if not isinstance(ref, str):
        raise StopNotFound(f"identifiant de gare illisible : {ref!r}")
    m = _STOP_AREA.match(ref)
    if not m:
        raise StopNotFound(f"ce n'est pas une gare (StopArea) : {ref!r}")
    return m.group(1)


def load_stops(csv_path):
    """Lit arrets_rerb_C01743.csv. Retourne {numéro: Stop}."""
    stops = {}
    with Path(csv_path).open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m = _STATIC.match(row["stop_id"])
            if not m:
                raise StopNotFound(f"stop_id inattendu : {row['stop_id']!r}")
            n = m.group(1)
            if n in stops:
                raise StopNotFound(f"gare en double dans le fichier : {n}")
            stops[n] = Stop(n, row["stop_name"],
                            float(row["stop_lat"]), float(row["stop_lon"]))
    return stops


def lookup(stops, ref):
    """Identifiant PRIM -> Stop. Lève StopNotFound si inconnu."""
    n = stop_area_number(ref)
    try:
        return stops[n]
    except KeyError:
        raise StopNotFound(f"gare {n} absente du fichier statique") from None


def stop_area_refs(data):
    """Gares distinctes (StopPointRef de type StopArea) d'une réponse PRIM déjà décodée."""
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "StopPointRef" and isinstance(v, dict):
                    val = v.get("value")
                    if isinstance(val, str) and val.startswith("STIF:StopArea:"):
                        found.add(val)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return sorted(found)
