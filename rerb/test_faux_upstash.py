"""Faux serveur REST Upstash pour les tests (T5). Aucun réseau, horloge simulée, aucun test ici.

Interprète les commandes que MagasinUpstash envoie : GET, MGET, SET (NX, PX, EX), MSET, INCR, EXPIRE et le script EVAL connu
(libération du verrou). Le script est REPRODUIT EN PYTHON (pas d'exécution Lua) : ce qu'il fait est vérifié ici, pas le texte Lua.
Compte les octets envoyés et reçus (mesure du transfert, exploration/budget_upstash.py).
Toute autre commande : HTTP 400, comme Upstash.
Pannes simulables : `panne` = "delai", "http500", "json" ou "forme".
"""
import json

from magasin_upstash import SCRIPT_LIBERER

JETON_FICTIF = "jeton-fictif-reconnaissable-7f3a"


class _Refus(Exception):
    """Commande que le faux serveur refuse (Upstash répond HTTP 400 avec {"error": ...})."""


class FauxUpstash:
    def __init__(self, horloge, jeton=JETON_FICTIF):
        self.horloge = horloge            # secondes ; sert aux expirations (PX, EX, EXPIRE)
        self.jeton = jeton
        self.valeurs = {}                 # clé -> (texte, instant d'expiration ou None)
        self.commandes = []               # chaque corps reçu, décodé (liste d'arguments)
        self.entetes = []                 # en-têtes reçus, pour vérifier l'autorisation
        self.panne = None                 # None, "delai", "http500", "json" ou "forme"
        self.octets_envoyes = 0           # corps des requêtes reçues (une commande = un corps)
        self.octets_recus = 0             # réponses renvoyées

    def _entree(self, cle):
        entree = self.valeurs.get(cle)
        if entree is not None and entree[1] is not None and entree[1] <= self.horloge():
            del self.valeurs[cle]
            return None
        return entree

    def _lire(self, cle):
        entree = self._entree(cle)
        return None if entree is None else entree[0]

    def http(self, url, corps, entetes, delai_s):
        """Même signature que le client HTTP injecté dans MagasinUpstash."""
        self.entetes.append(dict(entetes))
        if self.panne == "delai":
            raise TimeoutError(f"délai dépassé vers {url}")   # message porteur d'URL : le magasin ne doit pas le recopier
        if self.panne == "http500":
            return 500, b'{"error":"panne simulee"}'
        if self.panne == "json":
            return 200, b"<html>pas du JSON</html>"
        if self.panne == "forme":
            return 200, b'{"autre": 1}'
        if entetes.get("Authorization") != f"Bearer {self.jeton}":
            return 401, b'{"error":"Unauthorized"}'
        args = json.loads(corps)
        self.commandes.append(args)
        self.octets_envoyes += len(corps)
        try:
            statut, reponse = 200, json.dumps({"result": self._executer(args)}).encode("utf-8")
        except _Refus as erreur:
            statut, reponse = 400, json.dumps({"error": str(erreur)}).encode("utf-8")
        self.octets_recus += len(reponse)
        return statut, reponse

    def _executer(self, args):
        commande = args[0].upper()
        if commande == "GET":
            return self._lire(args[1])
        if commande == "MGET":
            return [self._lire(cle) for cle in args[1:]]
        if commande == "SET":
            return self._set(args)
        if commande == "MSET":
            for cle, valeur in zip(args[1::2], args[2::2]):
                self.valeurs[cle] = (valeur, None)
            return "OK"
        if commande == "INCR":
            return self._incr(args[1])
        if commande == "EXPIRE":
            return self._expirer(args[1], args[2])
        if commande == "EVAL":
            return self._eval(args)
        raise _Refus(f"commande inconnue : {commande}")

    def _set(self, args):
        cle, valeur, options = args[1], args[2], args[3:]
        expiration, nx, i = None, False, 0
        while i < len(options):
            option = str(options[i]).upper()
            if option == "NX":
                nx, i = True, i + 1
            elif option == "PX":
                expiration, i = self.horloge() + float(options[i + 1]) / 1000, i + 2
            elif option == "EX":
                expiration, i = self.horloge() + float(options[i + 1]), i + 2
            else:
                raise _Refus(f"option inconnue : {option}")
        if nx and self._entree(cle) is not None:
            return None
        self.valeurs[cle] = (valeur, expiration)
        return "OK"

    def _eval(self, args):
        script, nombre = args[1], int(args[2])
        cles, argv = args[3:3 + nombre], args[3 + nombre:]
        if script == SCRIPT_LIBERER:
            if self._lire(cles[0]) == argv[0]:
                del self.valeurs[cles[0]]
                return 1
            return 0
        raise _Refus("script inconnu")

    def _incr(self, cle):
        """INCR : ajoute 1, garde l'expiration éventuelle, crée la clé à 1 si absente."""
        entree = self._entree(cle)
        n = int(entree[0]) + 1 if entree is not None else 1
        expiration = entree[1] if entree is not None else None
        self.valeurs[cle] = (str(n), expiration)
        return n

    def _expirer(self, cle, duree):
        """EXPIRE : 1 si la clé existe (expiration posée), 0 sinon."""
        entree = self._entree(cle)
        if entree is None:
            return 0
        self.valeurs[cle] = (entree[0], self.horloge() + float(duree))
        return 1
