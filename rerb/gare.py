"""Tâche 4, J4 : prochains trains à une gare, depuis `stop-monitoring` (une gare par appel).

Décision de Mathis (plan tâche 4 §0.4) : le « prochain train » d'une gare vient de `stop-monitoring` direct (quota PRIM
1 000 000 par jour), pas de la carte. Cache de 60 s PAR GARE (la source ne change que toutes les ~30 s, sonde du 15/11).
• lire_passages  : réponse PRIM -> liste de passages du RER B (heure estimée, retard, à quai, annulé), triés, limités.
• ReleveurGares  : cache par gare, plafond d'appels commun, un seul appel à la fois par gare, erreur = dernier relevé gardé,
                   401/403/429 = plus aucun appel. Même logique que `Releveur` (serveur.py).
Les heures du cache sont refiltrées à chaque requête : un train parti disparaît sans nouvel appel.
Bibliothèque standard seulement ; la source (`fetch(gare)`) est injectée, les tests n'appellent jamais le réseau.
"""
import http.client
import threading
from datetime import datetime, timedelta

LIGNE = "STIF:Line::C01743:"
MARGE_DEPART = timedelta(seconds=60)   # un train parti il y a moins d'une minute reste affiché (relevé en retard de 20 à 30 s)
STATUTS_CLE_REFUSEE = (401, 403, 429)


def _valeur(x):
    return x.get("value") if isinstance(x, dict) else x


def _heure(texte):
    return datetime.fromisoformat(texte.replace("Z", "+00:00")) if texte else None


def _premier(liste):
    return _valeur(liste[0]) if isinstance(liste, list) and liste else None


def lire_passages(reponse, maintenant, limite=8):
    """Réponse `stop-monitoring` (ServiceDelivery) -> passages du RER B à venir, du plus proche au plus lointain.
    Lève ValueError si la structure n'est pas celle d'une réponse PRIM."""
    try:
        visites = reponse["StopMonitoringDelivery"][0].get("MonitoredStopVisit") or []
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ValueError("réponse stop-monitoring illisible") from None
    passages = []
    for v in visites:
        j = v.get("MonitoredVehicleJourney") or {}
        if _valeur(j.get("LineRef")) != LIGNE:
            continue
        appel = j.get("MonitoredCall") or {}
        estimee = _heure(appel.get("ExpectedDepartureTime") or appel.get("ExpectedArrivalTime"))
        if estimee is None:
            continue
        prevue = _heure(appel.get("AimedDepartureTime") or appel.get("AimedArrivalTime"))
        a_quai = bool(appel.get("VehicleAtStop"))
        if estimee < maintenant - MARGE_DEPART and not a_quai:
            continue
        passages.append({
            "course": _valeur((j.get("FramedVehicleJourneyRef") or {}).get("DatedVehicleJourneyRef")),
            "mission": _premier(j.get("VehicleJourneyName")),
            "destination": _premier(j.get("DestinationName")),
            "heure_estimee": estimee.isoformat(),
            "heure_prevue": prevue.isoformat() if prevue else None,
            "retard_s": (estimee - prevue).total_seconds() if prevue else None,
            "a_quai": a_quai,
            "annule": "cancelled" in (appel.get("DepartureStatus"), appel.get("ArrivalStatus")),
        })
    passages.sort(key=lambda p: p["heure_estimee"])
    return passages[:limite]


class ReleveurGares:
    """`fetch(gare)` -> réponse `stop-monitoring` décodée, ou lève ErreurPrim (serveur.py). `plafond` : voir Plafond."""

    def __init__(self, fetch, plafond, intervalle_s=60, limite=8):
        self._fetch, self._plafond = fetch, plafond
        self._intervalle, self._limite = timedelta(seconds=intervalle_s), limite
        self._verrou = threading.Lock()          # protège les dictionnaires ci-dessous
        self._en_cours = set()                   # gares dont l'appel est en cours
        self._cache = {}                         # gare -> (instant du relevé, réponse brute)
        self._tentative = {}                     # gare -> instant du dernier appel (réussi ou non)
        self._erreur = {}                        # gare -> message de la dernière erreur
        self.bloque = False

    @property
    def plafond(self):
        return self._plafond

    def _resultat(self, gare, maintenant, statut):
        with self._verrou:
            cache = self._cache.get(gare)
            erreur = self._erreur.get(gare)
        if cache is None:
            return {"statut": statut, "passages": [], "age_s": None, "erreur": erreur}
        recu, brut = cache
        return {"statut": statut, "passages": lire_passages(brut, maintenant, self._limite),
                "age_s": int((maintenant - recu).total_seconds()), "erreur": erreur}

    def passages(self, gare, maintenant):
        """-> {statut, passages, age_s, erreur}. statut : ok (appel fait), cache, plafond, bloqué, erreur, occupé."""
        from serveur import ErreurPrim   # import tardif : serveur.py importe ce module
        with self._verrou:
            if self.bloque:
                decision = "bloqué"
            elif gare in self._en_cours:
                decision = "occupé"
            elif gare in self._tentative and maintenant - self._tentative[gare] < self._intervalle:
                decision = "cache"
            elif not self._plafond.autorise():
                decision = "plafond"
            else:
                decision = "appeler"
                self._en_cours.add(gare)
                self._tentative[gare] = maintenant
                self._plafond.compter()
        if decision != "appeler":
            return self._resultat(gare, maintenant, decision)
        try:
            brut = self._fetch(gare)
            lire_passages(brut, maintenant)          # valide la structure avant de remplacer le cache
        except ErreurPrim as e:
            with self._verrou:
                self._erreur[gare] = str(e)
                self.bloque = e.statut in STATUTS_CLE_REFUSEE
            statut = "erreur"
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, http.client.HTTPException) as e:
            with self._verrou:
                self._erreur[gare] = f"réponse illisible ({type(e).__name__}: {e})" if not isinstance(e, ValueError) else f"réponse illisible ({e})"
            statut = "erreur"
        else:
            with self._verrou:
                self._cache[gare] = (maintenant, brut)
                self._erreur.pop(gare, None)
            statut = "ok"
        finally:
            with self._verrou:
                self._en_cours.discard(gare)
        return self._resultat(gare, maintenant, statut)
