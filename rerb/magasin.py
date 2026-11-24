"""Magasin : l'état partagé du serveur (verrou du relevé PRIM, compteurs d'appels, blocage, dernière tentative) derrière une seule interface.

Bibliothèque standard seulement. Deux implémentations :
• MagasinLocal : mémoire, compteurs aussi dans un fichier (un seul Mac, comportement d'avant T2).
• MagasinFaux  : même interface pour les tests, horloge injectée, pannes simulables.
T5 branchera un magasin Upstash sur la même interface. Une panne se signale par ErreurMagasin, jamais par une valeur par défaut.
"""
import json
import threading
import time
import uuid
from pathlib import Path


class ErreurMagasin(Exception):
    """Le magasin ne répond pas ou refuse. L'appelant ne fait alors RIEN qui coûte (aucun appel PRIM)."""


class Magasin:
    """Interface commune. Les sous-classes implémentent les six méthodes ; toute panne lève ErreurMagasin."""

    def prendre_verrou(self, nom, duree_s):
        """-> jeton (str) si le verrou est libre ou expiré, None s'il est tenu par un autre appel encore valable."""
        raise NotImplementedError

    def rendre_verrou(self, nom, jeton):
        """Libère le verrou si `jeton` en est encore le détenteur. Sans effet si le verrou a expiré (et a pu être repris)."""
        raise NotImplementedError

    def lire_compteur(self, cle):
        """Valeur entière du compteur, 0 s'il n'existe pas."""
        raise NotImplementedError

    def incrementer_compteur(self, cle):
        """Ajoute 1 au compteur et renvoie la nouvelle valeur."""
        raise NotImplementedError

    def lire(self, cle):
        """Valeur JSON stockée sous `cle`, ou None."""
        raise NotImplementedError

    def ecrire(self, cle, valeur):
        """Stocke une valeur JSON sous `cle`."""
        raise NotImplementedError


class MagasinLocal(Magasin):
    """Verrous et valeurs en mémoire ; compteurs en mémoire ET dans `fichier` (None : pas de fichier, pour la démo).

    # INVARIANT : seuls les compteurs sont écrits sur disque. Verrou et blocage (lire/ecrire) restent en mémoire : un blocage 401/403/429
    # ne survit pas à un redémarrage, comme avant T2 (sinon une clé refusée bloquerait le serveur sans fin).
    # INVARIANT : l'ancien fichier (`{"jour": ..., "n": ...}`, un seul jour) est lu comme le compteur `plafond:<jour>`. Sans cela, un
    # redémarrage après mise à jour remettrait à zéro les appels du jour et sous-estimerait le quota.
    """

    def __init__(self, fichier=None, horloge=None):
        self._fichier = Path(fichier) if fichier is not None else None
        self._horloge = horloge or time.monotonic   # secondes ; sert à l'expiration des verrous
        self._verrou = threading.Lock()              # protège tout l'état ci-dessous
        self._verrous = {}                           # nom -> (jeton, instant d'expiration)
        self._valeurs = {}
        self._compteurs = self._charger()

    def _charger(self):
        if self._fichier is None:
            return {}
        try:
            d = json.loads(self._fichier.read_text(encoding="utf-8"))
            if "compteurs" in d:
                return {str(k): int(v) for k, v in d["compteurs"].items()}
            if "jour" in d and "n" in d:
                return {f"plafond:{d['jour']}": int(d["n"])}
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            pass   # fichier absent ou illisible : compteurs à zéro
        return {}

    def _enregistrer(self):
        if self._fichier is None:
            return
        try:
            self._fichier.write_text(json.dumps({"compteurs": self._compteurs}), encoding="utf-8")
        except OSError:
            pass   # le compteur en mémoire reste valable pour ce démarrage (comportement d'avant T2)

    def prendre_verrou(self, nom, duree_s):
        with self._verrou:
            maintenant = self._horloge()
            actuel = self._verrous.get(nom)
            if actuel is not None and actuel[1] > maintenant:
                return None
            jeton = uuid.uuid4().hex
            self._verrous[nom] = (jeton, maintenant + duree_s)
            return jeton

    def rendre_verrou(self, nom, jeton):
        with self._verrou:
            actuel = self._verrous.get(nom)
            if actuel is not None and actuel[0] == jeton and actuel[1] > self._horloge():
                del self._verrous[nom]

    def lire_compteur(self, cle):
        with self._verrou:
            return self._compteurs.get(cle, 0)

    def incrementer_compteur(self, cle):
        with self._verrou:
            self._compteurs[cle] = self._compteurs.get(cle, 0) + 1
            self._enregistrer()
            return self._compteurs[cle]

    def lire(self, cle):
        with self._verrou:
            return self._valeurs.get(cle)

    def ecrire(self, cle, valeur):
        with self._verrou:
            self._valeurs[cle] = valeur


class MagasinFaux(MagasinLocal):
    """Magasin de test : mémoire seule, horloge simulée (`avancer`), pannes simulables.
    `panne = True` : toute méthode lève ErreurMagasin. `expirer(nom)` : le verrou `nom` expire tout de suite.
    `fixer(cle, n)` : place un compteur à `n` (plafond atteint sans boucler)."""

    def __init__(self):
        self.panne = False
        self.t = 0.0
        super().__init__(None, horloge=lambda: self.t)

    def _garde(self):
        if self.panne:
            raise ErreurMagasin("magasin simulé en panne")

    def avancer(self, secondes):
        self.t += secondes

    def expirer(self, nom):
        with self._verrou:
            if nom in self._verrous:
                self._verrous[nom] = (self._verrous[nom][0], self.t)

    def fixer(self, cle, n):
        with self._verrou:
            self._compteurs[cle] = n

    def prendre_verrou(self, nom, duree_s):
        self._garde()
        return super().prendre_verrou(nom, duree_s)

    def rendre_verrou(self, nom, jeton):
        self._garde()
        return super().rendre_verrou(nom, jeton)

    def lire_compteur(self, cle):
        self._garde()
        return super().lire_compteur(cle)

    def incrementer_compteur(self, cle):
        self._garde()
        return super().incrementer_compteur(cle)

    def lire(self, cle):
        self._garde()
        return super().lire(cle)

    def ecrire(self, cle, valeur):
        self._garde()
        return super().ecrire(cle, valeur)
