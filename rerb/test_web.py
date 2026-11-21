"""Tâche 4, J4 : la page de la carte n'appelle rien hors du serveur local (garde-fou statique, sans navigateur).

Complète la politique CSP envoyée par le serveur (test_serveur.py) et la garde réseau de carte.js : si quelqu'un
ajoute un CDN, une tuile ou une police en ligne dans la page, ce test échoue. Le fichier deck.gl vendu
(web/vendor) est exclu : son contenu est audité dans web/vendor/VERSIONS.md.
"""
import json
import math
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
FICHIERS = ("index.html", "carte.js", "carte.css", "modele-z8100.js")
URL_EXTERNE = re.compile(r"(https?:)?//[A-Za-z0-9.-]+\.[a-z]{2,}", re.I)


class TestPageLocale(unittest.TestCase):
    def test_fichiers_presents(self):
        for f in FICHIERS:
            self.assertTrue((WEB / f).is_file(), f)

    def test_aucune_adresse_externe(self):
        for f in FICHIERS:
            texte = (WEB / f).read_text(encoding="utf-8")
            # on retire les commentaires du code avant de chercher (ils peuvent citer des sites)
            texte = re.sub(r"/\*.*?\*/", "", texte, flags=re.S)
            texte = "\n".join(l for l in texte.splitlines() if not l.lstrip().startswith("//"))
            self.assertIsNone(URL_EXTERNE.search(texte), f"{f} : adresse externe trouvée")

    def test_ressources_de_index_toutes_locales_et_presentes(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        liens = re.findall(r'(?:src|href)="([^"]+)"', html)
        self.assertTrue(liens)
        for lien in liens:
            self.assertFalse(lien.startswith(("http", "//")), lien)
            self.assertTrue((WEB / lien).is_file(), lien)

    def test_deck_gl_vendu_a_la_version_notee(self):
        self.assertTrue((WEB / "vendor" / "deck.gl-9.4.1.min.js").is_file())
        self.assertIn("08857e84bc2e5478a62ce1dc59b9b5fae9ae0a21104c78503dece316d69a1ca6",
                      (WEB / "vendor" / "VERSIONS.md").read_text(encoding="utf-8"))

    def test_deck_gl_vendu_non_modifie(self):
        import hashlib
        h = hashlib.sha256((WEB / "vendor" / "deck.gl-9.4.1.min.js").read_bytes()).hexdigest()
        self.assertEqual(h, "08857e84bc2e5478a62ce1dc59b9b5fae9ae0a21104c78503dece316d69a1ca6")

    def test_la_page_annonce_la_position_estimee(self):
        self.assertIn("Position estimée, pas GPS", (WEB / "index.html").read_text(encoding="utf-8"))


class TestCodeCarte(unittest.TestCase):
    """Garde-fous statiques sur carte.js (le JS n'a pas de tests unitaires) : défauts réellement rencontrés."""

    def setUp(self):
        self.js = (WEB / "carte.js").read_text(encoding="utf-8")

    def test_les_trains_dessines_sont_les_positions_animees(self):
        corps = self.js.split("function calques(")[1]
        self.assertIn("placesAffiches(maintenant)", corps)
        self.assertEqual(self.js.count("placesAffiches("), 4, "définie une fois, utilisée dans calques(), pour retrouver un train à suivre et pour choisir le train de la vitrine")

    def test_aucune_donnee_prim_brute_dans_du_html(self):
        # toute valeur de train/gare insérée dans une chaîne HTML passe par esc()
        for ligne in self.js.splitlines():
            if "${t." in ligne:
                for champ in re.findall(r"\$\{(t\.[a-z_]+[^}]*)\}", ligne):
                    if champ in ("t.etat", "t.course", "t.gare", "t.motif", "t.gare_suivante") or champ.startswith("t.gare "):
                        self.fail(f"donnée PRIM non échappée : ${{{champ}}} dans : {ligne.strip()[:80]}")

    def test_pas_de_requetes_qui_s_empilent(self):
        self.assertIn("trainsEnCours", self.js)
        self.assertIn("gareEnCours", self.js)

    def test_incertitude_en_chemin_le_long_de_la_voie_plus_de_disque(self):
        corps = self.js.split("function calques(")[1]
        self.assertIn('"incertitude-p90"', corps)
        self.assertIn('"incertitude-mediane"', corps)
        self.assertIn("deck.PathLayer", corps.split('"incertitude-p90"')[1][:400])
        self.assertNotIn("getRadius: rayon", self.js, "plus de zone circulaire autour du train")
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('id="rayon"', html)
        self.assertNotIn("data-rayon", html)

    def _node(self, debut, fin, expression):
        """Exécute pour de vrai un bloc de carte.js (délimité par deux marqueurs) avec node, renvoie le JSON de `expression`."""
        import json, shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node absent")
        bloc = self.js.split(debut)[1].split(fin)[0]
        sortie = subprocess.run(["node", "-e", bloc + f"; console.log(JSON.stringify({expression}))"],
                                capture_output=True, text=True, timeout=30, check=True).stdout
        return json.loads(sortie)

    # --- tirets longs interdits sur le site (docs/anti-slop-design.md)
    def test_aucun_tiret_long_dans_le_site(self):
        for f in ("index.html", "carte.js", "carte.css", "modele-z8100.js"):
            texte = (WEB / f).read_text(encoding="utf-8")
            for car, nom in (("\u2013", "demi-cadratin"), ("\u2014", "cadratin"), ("\u2212", "signe moins")):
                self.assertNotIn(car, texte, f"{f} : {nom} interdit (utiliser le tiret simple)")

    def test_sans_tiret_long_nettoie_les_donnees_venues_de_prim(self):
        f = lambda s: self._node("// --- texte", "// --- fin texte", f"sansTiretLong({json.dumps(s)})")
        self.assertEqual(f("Châtelet \u2013 Les Halles"), "Châtelet - Les Halles")
        self.assertEqual(f("a\u2014b\u2212c"), "a-b-c")
        self.assertEqual(f("Massy-Palaiseau"), "Massy-Palaiseau")
        self.assertEqual(f(None), "")

    def test_les_donnees_affichees_passent_par_sansTiretLong(self):
        self.assertIn("sansTiretLong(x)", self.js.split("const esc =")[1].split("\n")[0])
        self.assertIn("sansTiretLong(texte)", self.js.split("function noeud")[1].split("\n")[0])
        self.assertIn("geo.gares.forEach", self.js)

    # --- chargement, pause de l'onglet caché, erreurs
    def _chargement(self, expression):
        return self._node("// --- chargement", "// --- fin chargement", expression)

    def test_chargement_message_puis_message_de_lenteur(self):
        base = {"recu": False, "attenteMs": 0, "erreur": None, "bloque": False, "reessai": False}
        e = self._chargement("etatChargement(%s)" % json.dumps(base))
        self.assertEqual((e["type"], e["reessayer"]), ("attente", False))
        self.assertIn("Chargement des trains du RER B", e["message"])
        e = self._chargement("etatChargement(%s)" % json.dumps({**base, "attenteMs": 8000}))
        self.assertEqual(e["type"], "lent")
        self.assertIn("PRIM met du temps à répondre", e["message"])

    def test_chargement_disparait_des_que_des_donnees_sont_recues(self):
        self.assertIsNone(self._chargement('etatChargement({recu: true, attenteMs: 99999, erreur: "x", bloque: true, reessai: false})'))

    def test_chargement_echec_propose_de_reessayer_et_bloque_non(self):
        e = self._chargement('etatChargement({recu: false, attenteMs: 500, erreur: "serveur local injoignable", bloque: false, reessai: false})')
        self.assertEqual((e["type"], e["reessayer"]), ("echec", True))
        self.assertIn("serveur local injoignable", e["message"])
        e = self._chargement('etatChargement({recu: false, attenteMs: 500, erreur: "HTTP 401", bloque: true, reessai: false})')
        self.assertEqual((e["type"], e["reessayer"]), ("bloque", False))

    def test_chargement_nouvel_essai_manuel_remet_le_message_d_attente(self):
        e = self._chargement('etatChargement({recu: false, attenteMs: 100, erreur: "x", bloque: false, reessai: true})')
        self.assertEqual(e["type"], "attente")

    def test_pastille_mise_a_jour_et_erreur_apres_les_premieres_donnees(self):
        f = lambda **k: self._chargement("etatDonnees(%s)" % json.dumps({"enCours": False, "ageS": 10, "erreur": None, "bloque": False, "reveil": False, **k}))
        self.assertIsNone(f())
        self.assertIsNone(f(enCours=True, ageS=10), "mise à jour de routine : rien à montrer")
        self.assertIn("Mise à jour", f(enCours=True, ageS=200)["message"])
        self.assertIn("Mise à jour", f(enCours=True, reveil=True)["message"], "retour sur l'onglet : on prévient")
        e = f(erreur="serveur local injoignable")
        self.assertEqual(e["type"], "erreur")
        self.assertIn("dernier relevé", e["message"])
        self.assertEqual(f(bloque=True, erreur="HTTP 401")["type"], "bloque")

    def test_onglet_cache_aucune_interrogation(self):
        self.assertIs(self._chargement("interrogationActive(true)"), False)
        self.assertIs(self._chargement("interrogationActive(false)"), True)

    def test_la_page_se_met_en_pause_et_reprend_au_retour(self):
        self.assertIn('addEventListener("visibilitychange"', self.js)
        for fonction in ("function actualiser()", "function actualiserGare()"):
            corps = self.js.split(fonction)[1][:600]
            self.assertIn("interrogationActive(document.hidden)", corps, fonction)

    def test_requete_trains_a_un_delai_maximal(self):
        corps = self.js.split("async function actualiser()")[1].split("let maj = false")[0]
        self.assertIn("AbortController", corps)
        self.assertIn("DELAI_REQUETE_MS", corps)

    def test_page_porte_l_ecran_de_chargement_accessible(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'id="chargement"[^>]*role="status"')   # reste le bloc d'état dans l'accueil
        self.assertIn('id="chargement-reessayer"', html)
        self.assertIn('id="etat-donnees"', html)
        css = (WEB / "carte.css").read_text(encoding="utf-8")
        self.assertIn("@keyframes avancer", css)
        self.assertIn("prefers-reduced-motion", css, "animation coupée si l'utilisateur le demande")

    # --- styles de carte à l'essai
    def _styles(self, expression):
        return self._node("// --- styles de carte", "// --- fin styles de carte", expression)

    def test_style_par_defaut_est_l_actuel_et_les_autres_sont_choisis_par_l_adresse(self):
        self.assertEqual(self._styles('styleDepuis("")'), "actuel")
        self.assertEqual(self._styles('styleDepuis("?style=inconnu")'), "actuel")
        for st in ("scan", "transport", "verre"):
            self.assertEqual(self._styles(f'styleDepuis("?style={st}")'), st)

    def test_chaque_style_a_une_palette_complete_et_le_rendu_actuel_est_intact(self):
        cles = self._styles("Object.keys(DECORS.scan).sort()")
        for st in ("transport", "verre"):
            self.assertEqual(self._styles(f"Object.keys(DECORS.{st}).sort()"), cles)
        self.assertIsNone(self._styles("RENDU.actuel"), "l'actuel garde son rendu d'origine")
        self.assertEqual(self._styles("DECORS.scan.fond"), "#000000")

    # --- rails visibles (style actuel)
    def _palette(self, expression):
        return self._node("// --- palette du décor", "// --- fin palette du décor", expression)

    @staticmethod
    def _lum(c):
        return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255

    def test_rails_se_distinguent_des_routes_dans_les_deux_themes(self):
        for theme, sens in (("sombre", 1), ("clair", -1)):
            p = self._palette(f"PALETTE.{theme}")
            rail = p["rail"]
            self.assertGreaterEqual(len(rail), 4, "le rail est translucide comme dans la version scan")
            routes = [self._lum(p[k]) for k in ("petit", "moyen", "grand", "pieton")]
            ecart = min(abs(self._lum(rail) - r) for r in routes)
            self.assertGreater(ecart, 0.25, f"thème {theme} : le rail doit trancher sur toutes les routes")
            if sens > 0:
                self.assertGreater(self._lum(rail), 0.9, "thème sombre : rails clairs")
            else:
                self.assertLess(self._lum(rail), 0.35, "thème clair : rails foncés")

    def test_rails_ont_leur_propre_calque_visible_de_loin(self):
        corps = self.js.split("function calquesDecor(")[1].split("// --- noms de gares")[0]
        self.assertIn('"rails-" + k', corps)
        rail = corps.split('"rails-" + k')[1][:400]
        m = re.search(r"widthMinPixels: ([0-9.]+)", rail)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(float(m.group(1)), 1.0)

    def test_les_rails_ne_sont_plus_dans_le_calque_des_routes(self):
        corps = self.js.split("function preparerCellule(")[1].split("function clesVisibles")[0]
        self.assertIn("rails:", corps)
        self.assertNotIn('t: "rail"', corps.split("lignes:")[1].split("rails:")[0], "les rails ne sont plus mêlés aux routes")

    def test_boutons_de_style_a_l_essai_retires_de_la_page(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('id="styles-carte"', html)
        self.assertIn("?style=", self.js, "le style reste choisissable par l'adresse")

    # --- accueil et interface en cartes flottantes
    def _accueil(self, expression):
        return self._node("// --- accueil", "// --- fin accueil", expression)

    def test_accueil_identique_a_chaque_chargement_bouton_actif_quand_les_trains_sont_la(self):
        self.assertEqual(self._accueil("etatAccueil({recu: false, ferme: false})"), {"afficher": True, "bouton": False})
        self.assertEqual(self._accueil("etatAccueil({recu: true, ferme: false})"), {"afficher": True, "bouton": True})

    def test_accueil_ferme_reste_ferme(self):
        self.assertFalse(self._accueil("etatAccueil({recu: true, ferme: true})")["afficher"])

    def test_rechargement_rejoue_toujours_la_meme_arrivee(self):
        # aucune mémoire entre deux chargements : la présentation et la vitrine 3D reviennent à chaque fois
        for interdit in ("sessionStorage", "localStorage", "accueil-vu", "accueilVu"):
            self.assertNotIn(interdit, self.js, interdit)
        self.assertIn("prefers-reduced-motion", self.js)

    def test_page_sans_panneau_lateral_avec_accueil_et_volets_repliables(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<aside", html)
        self.assertNotIn('id="panneau"', html)
        self.assertRegex(html, r'id="accueil"[^>]*role="dialog"')
        for ident in ("d-prochains", "d-legende", "d-options", "d-apropos"):
            m = re.search(rf'<details id="{ident}"([^>]*)>', html)
            self.assertIsNotNone(m, ident)
            self.assertNotIn("open", m.group(1), f"{ident} doit être replié au départ")
        self.assertNotIn('id="fiche-train"', html, "plus de fiche en coin : la bulle est reliée au train")
        self.assertNotIn('id="fiche-groupe"', html)

    def test_tous_les_identifiants_utilises_par_le_js_existent_dans_la_page(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        presents = set(re.findall(r'id="([^"]+)"', html))
        utilises = set(re.findall(r'\$\("([a-z0-9_-]+)"\)', self.js)) | set(re.findall(r'getElementById\("([a-z0-9_-]+)"\)', self.js))
        self.assertEqual(sorted(utilises - presents), [])

    def test_recherche_au_centre_en_haut(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        barre = html.split('id="barre-haut"')[1].split("</div>\n  <div id=\"bandeaux\"")[0]
        self.assertIn('id="recherche"', barre)
        self.assertIn('id="direction-changer"', barre)
        css = (WEB / "carte.css").read_text(encoding="utf-8")
        regle = css.split("#barre-haut")[1].split("}")[0]
        self.assertIn("left: 50%", regle)
        self.assertIn("translateX(-50%)", regle)

    # --- légende : une entrée par ligne, explication courte en début de ligne, espace entre les entrées
    def _legende(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        return html.split('id="legende"')[1].split("</details>")[0]

    def test_legende_une_entree_par_ligne_avec_explication_courte(self):
        bloc = self._legende()
        termes = re.findall(r"<dt[^>]*>(.*?)</dt>", bloc, re.S)
        explications = re.findall(r"<dd[^>]*>(.*?)</dd>", bloc, re.S)
        self.assertGreaterEqual(len(termes), 12, "tous les éléments de la carte sont expliqués")
        self.assertEqual(len(termes), len(explications), "chaque terme a son explication")
        for e in explications:
            self.assertLessEqual(len(e.split()), 12, f"explication trop longue : {e}")
            self.assertLessEqual(len(e), 90, e)
            self.assertTrue(e.endswith("."), e)
        self.assertNotIn('class="note"', bloc.split('id="source-decor"')[0], "plus de texte courant avec pastilles en ligne")

    def test_legende_espacee_et_alignee_a_gauche(self):
        css = self._css()
        entree = re.search(r"\.legende-liste \.entree\s*\{([^}]*)\}", css).group(1)
        self.assertGreaterEqual(float(re.search(r"margin-bottom:\s*(\d+)px", entree).group(1)), 8)
        dd = re.search(r"\.legende-liste dd\s*\{([^}]*)\}", css).group(1)
        self.assertRegex(dd, r"margin:\s*[^;]*0\s*;|margin-left:\s*0")

    # --- bulle reliée au train sélectionné par un trait
    def _bulle(self, expression):
        return self._node("// --- bulle", "// --- fin bulle", expression)

    CARTE = {"l": 1000, "h": 600}
    TAILLE = {"l": 300, "h": 200}

    def _place(self, point, zones=None, precedent=None, carte=None, taille=None):
        return self._bulle(f"placementBulle({json.dumps(point)}, {json.dumps(taille or self.TAILLE)}, {json.dumps(carte or self.CARTE)}, "
                           f"{json.dumps(zones or [])}, {json.dumps(precedent)})")

    def test_bulle_en_haut_a_droite_du_train_par_defaut(self):
        r = self._place({"x": 500, "y": 400})
        self.assertEqual(r["coin"], "hd")
        self.assertEqual((r["x"], r["y"]), (500 + 48, 400 - 48 - 200))
        self.assertEqual((r["trait"]["x1"], r["trait"]["y1"]), (500, 400), "le trait part du train")
        self.assertEqual((r["trait"]["x2"], r["trait"]["y2"]), (548, 352), "et arrive au coin le plus proche de la bulle")

    def test_bulle_change_de_cote_pres_des_bords(self):
        self.assertEqual(self._place({"x": 500, "y": 60})["coin"], "bd")
        self.assertEqual(self._place({"x": 960, "y": 400})["coin"], "hg")
        self.assertEqual(self._place({"x": 960, "y": 60})["coin"], "bg")

    def test_bulle_toujours_dans_la_carte(self):
        for point in ({"x": 5, "y": 5}, {"x": 995, "y": 595}, {"x": 500, "y": 300}, {"x": -50, "y": 700}):
            r = self._place(point)
            self.assertGreaterEqual(r["x"], 8); self.assertGreaterEqual(r["y"], 8)
            self.assertLessEqual(r["x"] + 300, 1000 - 8); self.assertLessEqual(r["y"] + 200, 600 - 8)
        petite = self._place({"x": 100, "y": 100}, carte={"l": 200, "h": 150})
        self.assertEqual((petite["x"], petite["y"]), (8, 8), "carte trop petite : bulle calée dans le coin, sans sortir")

    def test_bulle_evite_les_commandes(self):
        zone = {"x0": 480, "y0": 150, "x1": 900, "y1": 380}   # recouvre le coin haut droite
        self.assertNotEqual(self._place({"x": 500, "y": 400}, zones=[zone])["coin"], "hd")

    def test_bulle_ne_saute_pas_d_un_coin_a_l_autre(self):
        self.assertEqual(self._place({"x": 500, "y": 400}, precedent="hg")["coin"], "hg", "le coin précédent est gardé tant qu'il convient")
        self.assertEqual(self._place({"x": 100, "y": 400}, precedent="hg")["coin"], "hd", "mais on change quand il ne convient plus")

    def test_phrase_d_etat_en_une_ligne(self):
        f = lambda t: self._bulle(f"phraseEtat({json.dumps(t)})")
        self.assertEqual(f({"etat": "en route", "gare_suivante": "Antony"}), "En route vers Antony")
        self.assertEqual(f({"etat": "à quai", "gare": "Massy-Palaiseau"}), "À quai à Massy-Palaiseau")
        self.assertEqual(f({"etat": "à quai", "terminus": True, "gare": "Mitry-Claye"}), "Au terminus de Mitry-Claye, disparaît dans 60 s")
        self.assertEqual(f({"etat": "en route", "retenu": True, "gare_suivante": "Châtelet"}), "Retenu en pleine voie avant Châtelet")
        self.assertEqual(f({"etat": "en route"}), "En route")

    def test_la_bulle_est_placee_a_chaque_image_et_a_chaque_deplacement(self):
        self.assertIn("placerBulle()", self.js.split("function afficher(")[1].split("\n")[0])
        self.assertIn("placerBulle()", self.js.split("onViewStateChange")[1].split("onClick")[0])

    def test_le_trait_svg_se_cache_par_attribut_pas_par_la_propriete_hidden(self):
        # un élément SVG n'a pas de propriété `hidden` : l'écrire ne retire pas l'attribut et le trait restait invisible
        self.assertNotIn("trait.hidden", self.js)
        self.assertNotIn('$("trait-bulle").hidden', self.js)
        self.assertIn('toggleAttribute("hidden"', self.js)

    def test_la_bulle_remplace_les_fiches_du_coin(self):
        self.assertNotIn("fiche-train", self.js)
        self.assertNotIn("fiche-groupe", self.js)
        self.assertNotIn("fiche-corps", self.js)

    # --- arrivée en vitrine 3D : la caméra suit un train qui circule, sans incertitude, jusqu'au clic
    def _vitrine(self, expression):
        return self._node("// --- vitrine", "// --- fin vitrine", expression)

    def test_vitrine_choisit_le_train_en_route_le_plus_proche_du_centre(self):
        trains = [
            {"course": "loin", "lat": 48.86, "lon": 2.35, "etat": "en route"},
            {"course": "pres", "lat": 48.73, "lon": 2.27, "etat": "en route"},
            {"course": "terminus", "lat": 48.7255, "lon": 2.2588, "etat": "à quai", "terminus": True},
            {"course": "retenu", "lat": 48.7255, "lon": 2.2588, "etat": "en route", "retenu": True},
            {"course": "ancien", "lat": 48.7255, "lon": 2.2588, "etat": "en route", "donnees_anciennes": True},
            {"course": "sans-position", "lat": None, "lon": None, "etat": "en route"},
        ]
        self.assertEqual(self._vitrine(f"trainVitrine({json.dumps(trains)})"), "pres")

    def test_vitrine_repli_et_liste_vide(self):
        seul = [{"course": "T", "lat": 48.86, "lon": 2.35, "etat": "à quai", "terminus": True}]
        self.assertEqual(self._vitrine(f"trainVitrine({json.dumps(seul)})"), "T", "à défaut, n'importe quel train placé")
        self.assertIsNone(self._vitrine("trainVitrine([])"))
        self.assertIsNone(self._vitrine('trainVitrine([{"course": "X", "lat": null, "lon": null}])'))

    def test_vitrine_cap_de_la_camera_de_trois_quarts(self):
        self.assertEqual(self._vitrine("capVitrine(350)"), 25)
        self.assertEqual(self._vitrine("capVitrine(10)"), 45)
        self.assertEqual(self._vitrine("capVitrine(null)"), 35)

    def test_vitrine_a_chaque_chargement_jusqu_au_clic(self):
        f = lambda **k: self._vitrine("vitrineActive(%s)" % json.dumps({"ferme": False, "sortie": False, **k}))
        self.assertTrue(f())
        self.assertFalse(f(ferme=True))
        self.assertFalse(f(sortie=True), "dès que la carte part vers le haut, l'interface revient et l'incertitude aussi")

    def test_vitrine_sans_incertitude_et_interface_masquee(self):
        calques = self.js.split("function calques(")[1].split("\nfunction ")[0]
        self.assertIn("const modeAffiche = vitrineEnCours() ? \"train\" : mode;", calques)
        self.assertNotRegex(calques, r"\bmode (?:===|!==)", "le mode choisi n'est lu qu'à travers modeAffiche")
        self.assertIn('classList.toggle("vitrine"', self.js)
        css = self._css()
        self.assertRegex(css, r"#carte\.vitrine #dock")
        self.assertIn("#carte.vitrine #barre-haut", css)

    def test_vitrine_la_camera_continue_de_suivre_apres_le_clic(self):
        gestionnaire = self.js.split('$("accueil-entrer").addEventListener')[1].split("\n  });")[0]
        self.assertIn("accueilSortie = true", gestionnaire)
        self.assertNotIn("arreterSuivi", gestionnaire, "le clic ne coupe pas le suivi du train")
        self.assertNotIn("voler(", gestionnaire)

    def test_vitrine_la_carte_de_presentation_sort_vers_le_haut_sans_voile(self):
        css = self._css()
        regle = css.split("#accueil.sortie .carte-accueil")[1].split("}")[0]
        self.assertIn("translateY(", regle)
        self.assertIn("opacity: 0", regle)
        self.assertIn("transition:", css.split(".carte-accueil {")[1].split("}")[0])
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("matchMedia(\"(prefers-reduced-motion: reduce)\")", self.js.split('$("accueil-entrer").addEventListener')[1][:900])

    def test_vitrine_pas_de_vol_vers_toute_la_ligne_a_l_ouverture(self):
        ouverture = self.js.split("// ouverture :")[1].split("chargerDecor();")[0]
        self.assertNotIn("boiteLigne", ouverture)

    # --- design de la version jour (docs/anti-slop-design.md)
    POLICES_LOCALES = {"Barlow", "Barlow Semi Condensed", "IBM Plex Mono"}
    POLICES_BANNIES = ("system-ui", "-apple-system", "BlinkMacSystemFont", "Inter", "Roboto", "Poppins", "Space Grotesk", "DM Sans", "Arial", "Helvetica", "Segoe UI", "SF Pro")

    def _css(self):
        return (WEB / "carte.css").read_text(encoding="utf-8")

    def test_version_jour_seulement(self):
        self.assertNotIn("prefers-color-scheme", self._css())
        self.assertIn("matches: false", self.js.split("const sombre =")[1].split("\n")[0])

    def test_regles_design_pas_de_verre_de_degrade_ni_d_ombre_douce(self):
        css = self._css()
        self.assertNotIn("backdrop-filter", css)
        self.assertNotRegex(css, r"(linear|radial|conic)-gradient")
        self.assertNotIn("text-shadow", css)
        for valeur in re.findall(r"box-shadow:\s*([^;]+);", css):
            self.assertTrue(valeur.startswith("inset") or valeur.strip() == "none", f"ombre douce interdite : {valeur}")

    def test_regles_design_coins_droits(self):
        css = self._css()
        for selecteur, corps in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            for valeur in re.findall(r"border-radius:\s*([^;]+);", corps):
                for px in re.findall(r"(\d+(?:\.\d+)?)px", valeur):
                    self.assertLessEqual(float(px), 4, f"{selecteur.strip()} : coin trop arrondi")
                if "%" in valeur:
                    self.assertIn(".pastille", selecteur, "seules les pastilles d'état sont rondes")
        for v in re.findall(r"--rayon:\s*([^;]+);", css):
            self.assertLessEqual(float(re.match(r"\d+(?:\.\d+)?", v).group()), 4)

    def test_regles_design_polices_locales_et_aucune_police_par_defaut(self):
        css = self._css()
        for nom in self.POLICES_BANNIES:
            motif = r"(?<![A-Za-z-])" + re.escape(nom) + r"(?![A-Za-z])"
            self.assertIsNone(re.search(motif, css), f"police bannie : {nom}")
            self.assertIsNone(re.search(motif, self.js), f"police bannie : {nom}")
        for decl in re.findall(r"--f-(?:texte|titre|mono):\s*([^;]+);", css):
            premiere = decl.split(",")[0].strip().strip('"')
            self.assertIn(premiere, self.POLICES_LOCALES, decl)
        for decl in re.findall(r"font-family:\s*([^;]+);", css):
            self.assertTrue(decl.strip().startswith(('"', "var(")), decl)
        self.assertNotIn("fontFamily: \"", self.js, "la police de la carte vient de la variable CSS --f-carte")

    def test_les_fichiers_de_polices_existent_et_sont_notes(self):
        import hashlib
        notes = (WEB / "fonts" / "VERSIONS.md").read_text(encoding="utf-8")
        for url in re.findall(r"url\(([^)]+)\)", self._css()):
            f = WEB / url
            self.assertTrue(f.is_file(), url)
            self.assertEqual(f.read_bytes()[:4], b"wOF2", url)
            self.assertIn(hashlib.sha256(f.read_bytes()).hexdigest(), notes, f"{url} : somme SHA-256 non notée")

    def test_regles_design_ni_glyphes_d_icone_ni_pseudo_hud(self):
        for f in ("index.html", "carte.css", "carte.js"):
            texte = (WEB / f).read_text(encoding="utf-8")
            for g in "\u26a0\u270b\u27f2\u27f3\u2913\u2912\u25b2\u25b4\u25be\u2715":
                self.assertNotIn(g, texte, f"{f} : glyphe d'icône {g!r} interdit (SVG ou mot)")
            self.assertNotIn("reperes-vue", texte)
            self.assertNotIn("TARGET //", texte)

    def _variables(self):
        return dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", re.search(r":root\s*\{([^}]*)\}", self._css()).group(1)))

    @staticmethod
    def _contraste(a, b):
        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in c]
            return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]
        la, lb = sorted((lum(a), lum(b)), reverse=True)
        return (la + .05) / (lb + .05)

    def test_direction_signaletique_seule_et_textes_lisibles_sur_leur_surface(self):
        v = self._variables()
        for texte in ("--encre", "--encre-2", "--accent"):
            self.assertGreaterEqual(self._contraste(v[texte], v["--surface"]), 4.5, f"{texte} sur --surface")
        self.assertGreaterEqual(self._contraste(v["--encre"], v["--surface-2"]), 4.5)
        self.assertGreaterEqual(self._contraste(v["--principal-texte"], v["--principal-fond"]), 4.5, "bouton principal")
        for mort in ("data-ui", "atlas", "technique", "Source Serif", "Public Sans", "IBM Plex Sans"):
            self.assertNotIn(mort, self._css(), mort)
        for mort in ("interfaceDepuis", "INTERFACES", "dataset.ui"):
            self.assertNotIn(mort, self.js, mort)

    def _echelle(self, zoom):
        return self._node("// --- échelle du train", "// --- fin échelle du train", f"echelleTrain({zoom})")

    def test_de_pres_modele_detaille_a_la_taille_reelle(self):
        for z in (16, 17, 17.5, 19):
            self.assertEqual(self._echelle(z), {"detail": True, "longueur": 1, "largeur": 1}, f"zoom {z}")

    def test_de_loin_icone_agrandie(self):
        e = self._echelle(9.6)     # toute la ligne : ~66 m par pixel
        self.assertFalse(e["detail"], "au dézoom : icône, pas le modèle détaillé")
        self.assertGreater(e["longueur"], 10)
        self.assertGreater(e["largeur"], e["longueur"], "la rame est 25 fois plus longue que large : la largeur est agrandie davantage")
        self.assertLessEqual(e["longueur"], 60)
        self.assertLessEqual(e["largeur"], 120)

    def test_bascule_icone_detail_au_zoom_15(self):
        # seuil décidé le 18/11 (12 essayé : détaillé agrandi moins lisible que l'icône aux zooms 12 à 14)
        self.assertTrue(self._echelle(15)["detail"])
        self.assertFalse(self._echelle(14.9)["detail"])

    def test_detaille_elargi_en_continu_entre_les_zooms_16_et_15(self):
        largeurs = [self._echelle(z)["largeur"] for z in (16, 15.75, 15.5, 15.25, 15)]
        self.assertEqual(largeurs[0], 1)
        self.assertEqual(largeurs, sorted(largeurs))
        self.assertGreater(largeurs[-1], 4, "à 15, la caisse (2,8 m) mesure ~10 px de large")
        for z in (15.75, 15.5, 15):
            self.assertEqual(self._echelle(z)["longueur"], 1, "la longueur reste vraie : la rame fait déjà plus de 40 px")
        # pas de saut à la bascule : la largeur apparente de l'icône juste dessous est du même ordre
        icone = self._echelle(14.99)
        self.assertLess(abs(icone["largeur"] * 4.2 - largeurs[-1] * 2.798) / (icone["largeur"] * 4.2), 0.05)

    def test_echelle_croissante_en_dezoomant(self):
        valeurs = [self._echelle(z)["longueur"] for z in (15, 13, 11, 9.6, 8)]
        self.assertEqual(valeurs, sorted(valeurs))
        self.assertGreater(valeurs[-1], valeurs[0])

    def test_les_deux_niveaux_du_modele_sont_charges_et_la_page_charge_le_modele_avant_la_carte(self):
        self.assertIn("ModeleZ8100.construire({ icone: true })", self.js)
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index("modele-z8100.js"), html.index("carte.js"))

    def test_direction_nord_sud_d_apres_le_cap(self):
        d = lambda cap: self._node("// --- direction", "// --- fin direction", f"directionTrain({{cap: {cap}}})")
        self.assertEqual([d(0), d(45), d(89), d(300), d(359)], ["nord"] * 5)
        self.assertEqual([d(91), d(180), d(269)], ["sud"] * 3)
        self.assertIsNone(self._node("// --- direction", "// --- fin direction", "directionTrain({cap: null})"))

    def test_filtre_direction_garde_les_trains_sans_cap(self):
        g = lambda t, f: self._node("// --- direction", "// --- fin direction", f"garderTrain({t}, '{f}')")
        self.assertTrue(g("{cap: 10}", "tout"))
        self.assertTrue(g("{cap: 10}", "nord"))
        self.assertFalse(g("{cap: 10}", "sud"))
        self.assertTrue(g("{cap: 200}", "sud"))
        self.assertTrue(g("{cap: null}", "nord"), "cap inconnu : jamais masqué (on ne sait pas le classer)")

    def _regrouper(self, points, selection="null"):
        return self._node("// --- regroupement", "// --- fin regroupement", f"regrouper({json.dumps(points)}, {selection})")

    def test_regroupement_deux_trains_loin_restent_separes(self):
        r = self._regrouper([{"course": "A", "x": 0, "y": 0}, {"course": "B", "x": 100, "y": 0}])
        self.assertEqual((r["alpha"]["A"], r["alpha"]["B"]), (1, 1))
        self.assertEqual(r["bulles"], [])

    def test_regroupement_deux_trains_colles_une_bulle_2(self):
        r = self._regrouper([{"course": "B", "x": 3, "y": 0}, {"course": "A", "x": 0, "y": 0}])
        self.assertEqual((r["alpha"]["A"], r["alpha"]["B"]), (1, 0), "le chef = plus petit identifiant, quel que soit l'ordre d'entrée")
        self.assertEqual(len(r["bulles"]), 1)
        self.assertEqual((r["bulles"][0]["chef"], r["bulles"][0]["n"], r["bulles"][0]["alpha"]), ("A", 2, 1))
        self.assertEqual(r["bulles"][0]["membres"], ["A", "B"])

    def test_regroupement_continu_pas_de_saut_quand_ils_s_ecartent(self):
        prec = None
        for d in range(40, -1, -1):    # le 2e train s'approche de 1 px à chaque pas
            r = self._regrouper([{"course": "A", "x": 0, "y": 0}, {"course": "B", "x": d, "y": 0}])
            ab = r["alpha"]["B"]
            ba = r["bulles"][0]["alpha"] if r["bulles"] else 0
            if prec is not None:
                self.assertLessEqual(ab, prec[0] + 1e-9, f"d={d}")
                self.assertGreaterEqual(ba, prec[1] - 1e-9, f"d={d}")
                self.assertLess(abs(ab - prec[0]), 0.15, f"saut du train à d={d}")
                self.assertLess(abs(ba - prec[1]), 0.15, f"saut de la bulle à d={d}")
            prec = (ab, ba)
        self.assertEqual(prec, (0, 1))

    def test_regroupement_trois_trains_colles_bulle_3(self):
        pts = [{"course": c, "x": i * 2, "y": 0} for i, c in enumerate("ABC")]
        r = self._regrouper(pts)
        self.assertEqual(r["bulles"][0]["n"], 3)
        self.assertEqual([r["alpha"][c] for c in "ABC"], [1, 0, 0])

    def test_regroupement_train_choisi_jamais_masque(self):
        r = self._regrouper([{"course": "A", "x": 0, "y": 0}, {"course": "B", "x": 2, "y": 0}], selection='"B"')
        self.assertEqual(r["alpha"]["B"], 1)
        self.assertEqual(r["bulles"][0]["n"], 2)

    def test_trois_pas_d_empilement_ni_de_donnee_brute_dans_la_liste_du_groupe(self):
        self.assertIn("groupeOuvert", self.js)
        for ligne in self.js.splitlines():
            if "${m." in ligne and ("gare" in ligne or "motif" in ligne or "course" in ligne):
                self.fail(f"donnée PRIM non échappée dans la liste du groupe : {ligne.strip()[:80]}")

    def test_pastille_seulement_en_mode_incertitude_seule_et_couches_alpha(self):
        corps = self.js.split("function calques(")[1]
        self.assertIn("getScale", self.js.split("function calquesTrains(")[1].split("function calques(")[0])
        self.assertNotIn("ZOOM_PASTILLE", self.js)
        self.assertIn('modeAffiche === "zone"', corps.split('id: "pastilles"')[0][-120:])
        self.assertIn('id: "bulles"', corps)
        self.assertIn("calquesTrains(", corps)

    def test_zoom_redessine_les_trains_et_legende(self):
        self.assertIn("pasZoom", self.js)
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("modèle de la Z 8100", html, "la légende dit ce qui change au zoom")
        self.assertIn('id="direction-changer"', html, "la direction se change par le bouton du haut (plus de boutons radio dans un panneau)")

    def _interp(self, expression):
        return self._node("// --- interpolation", "// --- fin interpolation", expression)

    def test_mouvement_continu_sans_arret_entre_deux_releves(self):
        # retour de Mathis : « avance - pause - avance ». Vitesse constante sur tout le segment (pas de lissage à vitesse nulle aux bouts)
        e = "{de:[0,0,0], vers:[3,0,0], t0:0, duree:3000}"
        xs = [self._interp(f"positionAffichee({e}, {t})[0]") for t in (30, 300, 1500, 2700, 2970)]
        for x, t in zip(xs, (30, 300, 1500, 2700, 2970)):
            self.assertAlmostEqual(x, t / 1000, places=6, msg=f"t={t} ms")

    def test_cap_garde_le_dernier_connu_quand_le_serveur_n_en_donne_pas(self):
        # à quai au terminus le serveur n'envoie pas de cap : la rame ne doit pas se tourner vers le nord
        self.assertEqual(self._interp("capOuPrecedent(null, 123)"), 123)
        self.assertEqual(self._interp("capOuPrecedent(45, 123)"), 45)
        self.assertEqual(self._interp("capOuPrecedent(null, undefined)"), 0)

    def test_redessin_a_chaque_image_pas_toutes_les_60_ms(self):
        self.assertNotIn("requestAnimationFrame(pas), 60)", self.js)
        self.assertIn("requestAnimationFrame(pas)", self.js)

    def test_noms_de_gares_dessines_au_dessus_des_batiments(self):
        bloc = self.js.split('id: "noms-gares"')[1].split("}));")[0]
        self.assertIn("depthCompare", bloc)

    def _vue(self, expression):
        return self._node("// --- vue 3D", "// --- fin vue 3D", expression)

    def test_vue_inclinaison_et_rotation_bornees(self):
        self.assertEqual(self._vue("pasVue({bearing: 350, pitch: 80}, 30, 10)"), {"bearing": 20, "pitch": 85})
        self.assertEqual(self._vue("pasVue({bearing: 10, pitch: 5}, -30, -20)"), {"bearing": 340, "pitch": 0})
        self.assertEqual(self._vue("pasVue({bearing: 0, pitch: 55}, 0, 0)"), {"bearing": 0, "pitch": 55})

    def test_bandes_d_incertitude_ne_masquent_pas_le_pied_des_trains(self):
        # retour de Mathis : le bas des trains passait « sous » la bande d'incertitude (z-fighting : bandes coplanaires au sol, écrivant la profondeur)
        bloc = self.js.split('id, data: p.filter(d => avecChemin(d, cle))')[1].split("}));")[0]
        self.assertIn("depthWriteEnabled: false", bloc, "la bande ne doit pas écrire dans le tampon de profondeur")
        self.assertIn("Z_BANDE", bloc, "chaque bande a sa propre hauteur (pas de plans coplanaires)")
        z = self._node("// --- hauteurs des bandes", "// --- fin hauteurs des bandes", "Z_BANDE")
        self.assertLess(z["p90"], z["mediane"], "la bande étroite (médiane) au-dessus de la large (p90)")
        self.assertGreater(z["p90"], 0)
        self.assertLess(z["mediane"], 0.6, "sous le plancher de la rame (0,6 m), sinon la bande traverse le train")

    def test_vue_initiale_memorise_l_inclinaison_reelle(self):
        # sinon le 1er clic sur « incliner » repartirait de 0° au lieu de 55° et la carte s'aplatirait d'un coup
        self.assertRegex(self.js, r"vueCarte = \{ longitude: 2\.2588, latitude: 48\.7255, zoom: 14, pitch: 55, bearing: 0 \}")

    def test_vue_controles_dans_la_page(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        for ident in ("boussole", "incliner-plus", "incliner-moins", "tourner-gauche", "tourner-droite"):
            self.assertIn(f'id="{ident}"', html, ident)
        self.assertIn("MAX_PITCH = 85", self.js)
        self.assertIn("maxPitch: MAX_PITCH", self.js)
        self.assertIn("bearing: v.bearing || 0", self.js, "la vue mémorise le cap et l'inclinaison")
        self.assertIn("clic droit", html)

    # --- retours de Mathis du 17/11 sur la carte

    def test_vue_de_depart_assez_proche_pour_voir_les_batiments(self):
        zd = float(re.search(r"ZOOM_DEPART = ([0-9.]+)", self.js).group(1))
        zdecor = float(re.search(r"ZOOM_DECOR = ([0-9.]+)", self.js).group(1))
        self.assertGreater(zd, zdecor, "au chargement, le décor (bâtiments) doit déjà s'afficher")

    def test_glisser_deplace_clic_droit_ou_ctrl_tourne_sans_bouton_de_mode(self):
        self.assertIn('dragMode: "pan"', self.js, "le glisser déplace toujours (Google Maps, Apple Plans, Google Earth)")
        self.assertIn("dragRotate: true", self.js, "clic droit ou Ctrl + glisser tournent")
        self.assertIn("touchRotate: true", self.js, "deux doigts tournent sur écran tactile")
        for mort in ("modeGlisser", "reglerGlisser", "mode-glisser"):
            self.assertNotIn(mort, self.js, f"plus de bascule de mode : {mort}")
        self.assertIn("inertia", self.js)
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("mode-glisser", html)
        for ident in ("zoom-plus", "zoom-moins", "boussole-carte", "incliner-carte"):
            self.assertIn(f'id="{ident}"', html, ident)

    def test_deltaVue_glisser_horizontal_tourne_vertical_incline(self):
        d = self._vue("deltaVue(100, 0)")
        self.assertGreater(d["dBearing"], 0)
        self.assertEqual(d["dPitch"], 0)
        d = self._vue("deltaVue(0, -100)")
        self.assertEqual(d["dBearing"], 0)
        self.assertGreater(d["dPitch"], 0, "glisser vers le haut : on se rapproche de l'horizontale")
        self.assertLess(self._vue("deltaVue(0, 100)")["dPitch"], 0)

    def test_deltaVue_respecte_les_bornes_via_pasVue(self):
        r = self._vue("(() => { const d = deltaVue(0, -10000); return pasVue({bearing: 0, pitch: 40}, d.dBearing, d.dPitch); })()")
        self.assertEqual(r["pitch"], 85)
        r = self._vue("(() => { const d = deltaVue(10000, 0); return pasVue({bearing: 0, pitch: 40}, d.dBearing, d.dPitch); })()")
        self.assertTrue(0 <= r["bearing"] < 360)

    def test_boussole_se_glisse_et_un_clic_remet_le_nord(self):
        corps = self.js
        self.assertIn('$("boussole-carte").addEventListener("pointerdown"', corps)
        self.assertIn("setPointerCapture", corps)
        self.assertIn("deltaVue(", corps.split('"boussole-carte").addEventListener("pointerdown"')[1])
        css = (WEB / "carte.css").read_text(encoding="utf-8")
        self.assertIn("touch-action: none", css.split("#boussole-carte")[1][:300])

    def test_zoom_molette_ne_coupe_pas_le_suivi(self):
        # deck.gl annonce isPanning aussi pendant un zoom à la molette (le point sous la souris bouge) : seul un vrai glisser arrête le suivi
        self.assertIn("i.isDragging && i.isPanning && !i.isZooming", self.js)

    def test_decor_par_cellule_pas_de_refonte_globale(self):
        # avant : toutes les cellules fusionnées en un seul jeu de données, tout recalculé à chaque cellule arrivée (saccades au déplacement)
        self.assertIn('id: "surfaces-" + k', self.js)
        self.assertIn('id: "batiments-" + k', self.js)
        self.assertNotIn("flatMap", self.js.split("function calquesDecor")[1].split("\n}\n")[0], "pas de fusion de toutes les cellules")
        self.assertIn("cellulesAMontrer", self.js, "une seule nouvelle cellule par image")

    def test_clic_gare_zoome_clic_train_suit_et_affiche_la_fiche(self):
        corps = self.js.split("onClick: ({ object, layer })")[1].split("});\n}")[0]
        self.assertIn('layer.id === "noms-gares"', corps)
        self.assertIn("allerGare(object)", corps)
        self.assertIn("suivreTrain(object.course)", corps)
        self.assertIn("pickable: true", self.js.split('id: "noms-gares"')[1].split("}));")[0], "le nom de la gare est cliquable")
        html = (WEB / "index.html").read_text(encoding="utf-8")
        for ident in ("bulle", "trait-bulle", "bulle-corps", "fiche-suivi", "bulle-fermer", "bulle-zone"):
            self.assertIn(f'id="{ident}"', html, ident)

    def test_une_seule_direction_a_la_fois_avec_bouton_et_texte_sur_la_carte(self):
        self.assertIn('filtreDirection = "nord"', self.js)
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="direction-texte"', html)
        self.assertIn('id="direction-changer"', html)
        self.assertNotIn('value="tout"', html)
        self.assertIn("Trains affichés : ", self.js)

    def test_train_au_terminus_rouge_avec_contour_qui_pulse(self):
        self.assertIn("t.terminus ? ROUGE_TERMINUS", self.js)
        self.assertNotIn('id: "terminus-halo"', self.js, "le halo circulaire est remplacé par le contour en gélule")
        self.assertIn("trains.some(t => t.terminus || t.retenu)", self.js, "le contour pulse : l'animation continue tant qu'un train est au terminus ou retenu")

    # --- contour en gélule autour de la rame (maquette validée le 18/11)

    def _contour(self, expression):
        import re, shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node absent")
        bloc = lambda a, b: self.js.split(a)[1].split(b)[0]
        constantes = " ".join(re.search(rf"const {n} = \[[^\]]*\];", self.js).group(0) for n in ("ROSE_ANCIENNES", "JAUNE_RETENU", "COULEUR_SELECTION"))
        code = (constantes + bloc("// --- échelle du train", "// --- fin échelle du train") + bloc("// --- voitures", "// --- fin voitures")
                + bloc("// --- contour", "// --- fin contour") + f"; console.log(JSON.stringify({expression}))")
        return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout)

    def test_etats_qui_dessinent_un_contour_et_leur_ordre(self):
        c = lambda t, sel: [x["couleur"] for x in self._contour(f"contoursDeTrain({json.dumps(t)}, {json.dumps(sel)})")]
        self.assertEqual(c({}, False), [], "en route, à quai, horaire théorique : pas de contour")
        self.assertEqual(c({"horaire_theorique": True}, False), [])
        self.assertEqual(c({"terminus": True}, False), [[255, 60, 75]])
        self.assertEqual(c({"donnees_anciennes": True}, False), [[236, 72, 153]])
        self.assertEqual(c({"retenu": True}, False), [[250, 204, 21]], "retenu en pleine voie : jaune, différent du violet de la sélection")
        self.assertEqual(c({"terminus": True, "retenu": True, "donnees_anciennes": True}, True),
                         [[255, 60, 75], [250, 204, 21], [168, 85, 247], [236, 72, 153]], "priorité : terminus > retenu > sélection > données anciennes")
        self.assertEqual(c({}, True), [[168, 85, 247]], "la sélection est violette (ni ambre ni jaune)")
        # cumul : le plus important au plus près de la rame
        self.assertEqual(c({"terminus": True, "donnees_anciennes": True}, True), [[255, 60, 75], [168, 85, 247], [236, 72, 153]])
        self.assertTrue(self._contour('contoursDeTrain({terminus: true}, false)[0].pulse'))
        self.assertFalse(self._contour('contoursDeTrain({}, true)[0].pulse'))

    def test_gelule_a_distance_constante_de_l_axe(self):
        # axe en arc de 150 m de rayon : chaque point du contour est à r de l'axe (côtés parallèles, bouts arrondis)
        R, r = 150.0, 2.5
        axe = [[R * (1 - math.cos(a)), R * math.sin(a)] for a in [math.radians(k * 40 / 12) for k in range(13)]]
        pts = self._contour(f"gelule({json.dumps(axe)}, {r})")
        self.assertEqual(pts[0], pts[-1], "contour fermé")
        def dist_axe(p):
            d = 1e9
            for a, b in zip(axe, axe[1:]):
                ex, ey, L2 = b[0] - a[0], b[1] - a[1], (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
                t = max(0, min(1, ((p[0] - a[0]) * ex + (p[1] - a[1]) * ey) / L2))
                d = min(d, math.hypot(p[0] - a[0] - t * ex, p[1] - a[1] - t * ey))
            return d
        for p in pts:
            self.assertAlmostEqual(dist_axe(p), r, delta=0.08)
        # bouts arrondis : le contour dépasse l'axe de r à chaque bout, dans l'axe
        self.assertGreater(max(math.hypot(p[0] - axe[0][0], p[1] - axe[0][1]) for p in pts), r - 0.01)

    def test_axe_de_la_rame_suit_la_voie_en_courbe(self):
        R, ky, kx = 150.0, 110540.0, 111320 * math.cos(math.radians(48.8))
        arc = [[2 + R * (1 - math.cos(a)) / kx, 48.8 + R * math.sin(a) / ky] for a in [math.radians(k * 90 / 60) for k in range(61)]]
        milieu = arc[30]
        axe = self._contour(f"axeSurVoie({json.dumps(arc)}, {milieu[0]}, {milieu[1]}, 50.6)")
        self.assertGreaterEqual(len(axe), 13, "assez de points pour suivre la courbe")
        centre = [(2 + R / kx - milieu[0]) * kx, (48.8 - milieu[1]) * ky]   # centre du cercle, en mètres autour du centre de la rame
        for x, y in axe:
            self.assertAlmostEqual(math.hypot(x - centre[0], y - centre[1]), R, delta=0.2)
        self.assertAlmostEqual(math.hypot(axe[-1][0] - axe[0][0], axe[-1][1] - axe[0][1]), 2 * R * math.sin(101.2 / (2 * R)), delta=0.3)
        self.assertIsNone(self._contour("axeSurVoie([], 2, 48.8, 50)"))

    def test_carte_dessine_socle_et_contours_et_plus_les_anciens_cercles(self):
        for couche in ('id: "rames-socle"', 'id: "rames-contour"'):
            self.assertIn(couche, self.js)
        self.assertNotIn("[245, 158, 11]", self.js, "l'ambre de l'ancien anneau de sélection est retiré")
        self.assertIn("COULEUR_SELECTION = [168, 85, 247]", self.js)
        self.assertIn("PathLayer", self.js)

    def test_palette_de_l_icone_alignee_sur_le_detaille(self):
        self.assertIn("PALETTE_ICONE = { bleu: [84, 100, 146], noir: [52, 56, 62], blanc: [208, 214, 222] }", self.js)
        self.assertIn("PALETTE_ICONE[piece.nom] || piece.couleur", self.js)

    # --- rame articulée : chaque caisse sur la voie

    def _voitures(self, expression):
        return self._node("// --- voitures", "// --- fin voitures", expression)

    @staticmethod
    def _metres(a, b, lat=48.8):
        return math.hypot((b["lon"] - a["lon"]) * 111320 * math.cos(math.radians(lat)), (b["lat"] - a["lat"]) * 110540)

    def test_rame_droite_sur_voie_droite(self):
        # voie vers le nord, 400 m : quatre caisses alignées, espacées de 26 m, cap nord, la motrice de tête devant
        c = self._voitures("voituresSurVoie([[2, 48.7982], [2, 48.8018]], 2, 48.8)")
        self.assertEqual(len(c), 4)
        for k in range(3):
            self.assertAlmostEqual(self._metres(c[k], c[k + 1]), 26.0 if k == 1 else 26.0125, delta=0.2)
        self.assertTrue(all(abs(v["lon"] - 2) < 1e-7 and (v["cap"] < 0.01 or v["cap"] > 359.99) for v in c))
        self.assertGreater(c[0]["lat"], c[1]["lat"], "la tête (rang 0) est devant : au nord")
        self.assertGreater(c[2]["lat"], c[3]["lat"])
        self.assertAlmostEqual(self._metres({"lon": 2, "lat": 48.8}, {"lon": 2, "lat": (c[1]["lat"] + c[2]["lat"]) / 2}), 0, delta=0.05, msg="rame centrée sur la position du train")

    def test_rame_se_plie_dans_une_courbe(self):
        # arc de cercle de 150 m de rayon (de 0° à 90°), le train au milieu de l'arc : chaque caisse tourne un peu plus que la précédente
        R, ky, kx = 150.0, 110540.0, 111320 * math.cos(math.radians(48.8))
        arc = [[2 + R * (1 - math.cos(a)) / kx, 48.8 + R * math.sin(a) / ky] for a in [math.radians(k * 90 / 60) for k in range(61)]]
        milieu = arc[30]
        c = self._voitures(f"voituresSurVoie({json.dumps(arc)}, {milieu[0]}, {milieu[1]})")
        caps = [v["cap"] for v in c]
        self.assertTrue(all(caps[i] > caps[i + 1] for i in range(3)) or all(caps[i] < caps[i + 1] for i in range(3)), f"caps monotones d'une caisse à l'autre : {caps}")
        total = abs(caps[0] - caps[3])
        # 3 x 26 m d'arc sur 150 m de rayon : ~29,8° entre les centres de la tête et de la queue
        self.assertAlmostEqual(total, math.degrees(78.0 / R), delta=2.0)
        for v in c:   # chaque centre de caisse est à moins de 25 cm de l'arc (flèche de la corde de 18,5 m : 0,29 m, la moitié de ce qu'une rame rigide de 104 m aurait)
            self.assertAlmostEqual(math.hypot((v["lon"] - 2 - R / kx) * kx, (v["lat"] - 48.8) * ky), R, delta=0.4)

    def test_position_hors_de_la_voie_prolongee_en_ligne_droite(self):
        # le train est 30 m avant le début du tracé : la voie est prolongée tout droit, rien ne se bloque au début
        voie = "[[2, 48.8], [2, 48.8036]]"
        avant = self._voitures(f"voituresSurVoie({voie}, 2, 48.8 - 30 / 110540)")
        dedans = self._voitures(f"voituresSurVoie({voie}, 2, 48.8 + 60 / 110540)")
        self.assertAlmostEqual(avant[0]["lat"], 48.8 - 30 / 110540 + 39.0125 / 110540, places=6)
        self.assertAlmostEqual(dedans[0]["lat"], 48.8 + 60 / 110540 + 39.0125 / 110540, places=6)

    def test_voie_absente_ou_degeneree(self):
        self.assertIsNone(self._voitures("voituresSurVoie(null, 2, 48.8)"))
        self.assertIsNone(self._voitures("voituresSurVoie([[2, 48.8]], 2, 48.8)"))
        self.assertIsNone(self._voitures("voituresSurVoie([[2, 48.8], [2, 48.8]], 2, 48.8)"))

    def test_sans_voie_la_rame_est_droite_selon_le_cap(self):
        # pas de voie (terminus, serveur sans tracé) : voie fictive dans la direction du cap -> comportement de l'ancienne rame rigide
        c = self._voitures("voitures([{ course: 'X', lon: 2, lat: 48.8, cap: 90, voie: null, alpha: 1 }])")
        self.assertEqual([v["rang"] for v in c], [0, 1, 2, 3])
        self.assertTrue(all(abs(v["cap"] - 90) < 0.01 for v in c))
        self.assertGreater(c[0]["lon"], c[3]["lon"], "cap 90 = vers l'est : la tête est à l'est")
        self.assertTrue(all(v["course"] == "X" for v in c), "chaque caisse porte le train (clic, info-bulle)")

    def test_les_caisses_arriere_sont_tournees_de_180_degres(self):
        o = self._voitures("[0, 1, 2, 3].map(rang => orientationVoiture({ cap: 90, rang }))")
        self.assertEqual([x[1] % 360 for x in o], [0, 0, 180, 180])

    def test_la_carte_utilise_les_caisses_de_pres_et_l_icone_de_loin(self):
        corps = self.js.split("function calquesTrains(")[1].split("function calques(")[0]
        self.assertIn("voitures(p)", corps)
        self.assertIn("getOrientation: orientationVoiture", corps)
        self.assertIn("MODELES.icone", corps)
        self.assertIn("t.voie", self.js.split("function majAffiches")[1].split("function placesAffiches")[0], "la voie est gardée entre deux relevés")

    def _noms(self, expression):
        import shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node absent")
        bloc = self.js.split("// --- noms de gares")[1].split("// --- fin noms de gares")[0]
        sortie = subprocess.run(["node", "-e", "const sombre = { matches: false };" + bloc + f"; console.log(JSON.stringify({expression}))"],
                                capture_output=True, text=True, timeout=30, check=True).stdout
        return json.loads(sortie)

    def test_noms_de_gares_lisibles_sur_fond_clair_et_sombre(self):
        lum = lambda c: (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255
        c = self._noms("COULEUR_NOM")
        self.assertLess(lum(c["clair"]["texte"]), 0.2, "thème clair : texte sombre")
        self.assertGreater(lum(c["clair"]["contour"]), 0.8, "thème clair : contour clair")
        self.assertGreater(lum(c["sombre"]["texte"]), 0.8, "thème sombre : texte clair (le noir sur fond noir n'était pas lisible)")
        self.assertLess(lum(c["sombre"]["contour"]), 0.2, "thème sombre : contour sombre")

    def _vol(self, expression):
        interp = self.js.split("// --- interpolation")[1].split("// --- fin interpolation")[0]
        import json, shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node absent")
        bloc = self.js.split("// --- vol de caméra")[1].split("// --- fin vol de caméra")[0]
        sortie = subprocess.run(["node", "-e", interp + bloc + f"; console.log(JSON.stringify({expression}))"],
                                capture_output=True, text=True, timeout=30, check=True).stdout
        return json.loads(sortie)

    def test_vol_de_camera_part_de_la_vue_et_arrive_a_la_cible(self):
        de = "{longitude: 2, latitude: 48, zoom: 10, pitch: 50, bearing: 350}"
        vers = "{longitude: 3, latitude: 49, zoom: 17, pitch: 60, bearing: 10}"
        debut = self._vol(f"etapeVol({de}, {vers}, 0, 1.5)")
        fin = self._vol(f"etapeVol({de}, {vers}, 1, 1.5)")
        milieu = self._vol(f"etapeVol({de}, {vers}, 0.5, 0)")
        self.assertEqual((debut["longitude"], debut["zoom"], debut["bearing"]), (2, 10, 350))
        self.assertEqual((fin["longitude"], fin["latitude"], fin["zoom"]), (3, 49, 17))
        self.assertAlmostEqual((fin["bearing"] + 360) % 360, 10, places=6)
        self.assertAlmostEqual(milieu["longitude"], 2.5, places=6)
        self.assertAlmostEqual((milieu["bearing"] + 360) % 360, 0, places=6, msg="le cap tourne par le plus court chemin (350° -> 10° passe par 0°)")

    def test_vol_long_dezoome_en_chemin(self):
        de = "{longitude: 2, latitude: 48, zoom: 15, pitch: 50, bearing: 0}"
        sans = self._vol(f"etapeVol({de}, {de}, 0.5, 0)")["zoom"]
        avec = self._vol(f"etapeVol({de}, {de}, 0.5, 2)")["zoom"]
        self.assertAlmostEqual(sans - avec, 2, places=6)

    def _gares(self, saisie):
        gares = [{"ref": "1", "nom": "Châtelet - Les Halles"}, {"ref": "2", "nom": "Gare du Nord"}, {"ref": "3", "nom": "Massy - Palaiseau"},
                 {"ref": "4", "nom": "Massy - Verrières"}, {"ref": "5", "nom": "Le Guichet"}, {"ref": "6", "nom": "Cité Universitaire"}]
        return [g["nom"] for g in self._node("// --- recherche de gare", "// --- fin recherche de gare", f"suggestionsGares({json.dumps(gares)}, {json.dumps(saisie)})")]

    def test_recherche_sans_accent_ni_majuscule(self):
        self.assertEqual(self._gares("chatel"), ["Châtelet - Les Halles"])
        self.assertEqual(self._gares("CITE univ"), ["Cité Universitaire"])
        self.assertEqual(self._gares("gare du nord"), ["Gare du Nord"])

    def test_recherche_debut_de_nom_avant_milieu_et_vide_sans_saisie(self):
        self.assertEqual(self._gares("massy"), ["Massy - Palaiseau", "Massy - Verrières"])
        self.assertEqual(self._gares("gu"), ["Le Guichet"], "début d'un mot")
        self.assertEqual(self._gares("ni")[0:1], ["Cité Universitaire"], "contenu du nom, après les débuts de nom ou de mot")
        self.assertEqual(self._gares("nord")[0], "Gare du Nord", "début d'un mot")
        self.assertEqual(self._gares("   "), [])
        self.assertEqual(self._gares("zzz"), [])

    def test_donnees_anciennes_et_horaire_theorique_expliques(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("15 min", html)
        self.assertIn("données anciennes", html.lower())
        self.assertIn("horaire théorique", html.lower())
        self.assertIn("aucune nouvelle de PRIM depuis plus de 15 min", self.js)
        self.assertIn("n'annonce aucun écart", self.js)
        # les notes ne prétendent pas plus que ce qu'on sait (décision du 18/11, piste A)
        self.assertIn("extrapolée de la dernière estimation", self.js)
        self.assertNotIn("vient de l'horaire seul", self.js)
        self.assertIn("à l'heure, ou non suivi en temps réel", self.js)
        self.assertIn("soit à l'heure, soit non suivi en direct par prim", html.lower())

    def test_le_mot_perime_a_disparu_de_la_page(self):
        # le mot était trompeur (décision du 18/11) ; le champ JSON `perime` reste servi un temps, mais la page ne le lit plus
        html = (WEB / "index.html").read_text(encoding="utf-8")
        for nom, texte in (("index.html", html), ("carte.js", self.js)):
            self.assertNotIn("périm", texte.lower(), nom)
            self.assertNotIn(".perime", texte, nom)

    def _etiquettes(self, t):
        return self._node("// --- etiquettes", "// --- fin etiquettes", f"etiquettes({json.dumps(t)})")

    def test_etiquettes_d_un_train(self):
        self.assertEqual(self._etiquettes({"etat": "en route"}), [])
        (e,) = self._etiquettes({"donnees_anciennes": True})
        self.assertEqual(e["libelle"], "Données anciennes")
        self.assertIn("15 min", e["note"])
        self.assertIn("extrapolée", e["note"])
        (e,) = self._etiquettes({"horaire_theorique": True})
        self.assertEqual(e["libelle"], "Horaire théorique")
        self.assertIn("n'annonce aucun écart", e["note"])
        self.assertIn("non suivi en temps réel", e["note"])
        deux = self._etiquettes({"donnees_anciennes": True, "horaire_theorique": True})
        self.assertEqual([x["libelle"] for x in deux], ["Données anciennes", "Horaire théorique"])

    def test_couleur_rose_seulement_pour_des_donnees_anciennes(self):
        c = lambda t: self._node("// --- Couleur et orientation", "// --- etiquettes",
                                 f"(globalThis.ROSE_ANCIENNES = 'rose', globalThis.GRIS = 'gris', globalThis.ROUGE_TERMINUS = 'rouge', globalThis.COULEURS = {{'en route': 'bleu'}}, couleurTrain({json.dumps(t)}))")
        gris = c({"donnees_anciennes": True, "etat": "en route"})
        self.assertEqual(c({"horaire_theorique": True, "etat": "en route"}), c({"etat": "en route"}), "horaire théorique : pas de couleur d'alerte")
        self.assertNotEqual(gris, c({"etat": "en route"}))

    def test_batiments_a_hauteur_par_defaut_ont_une_teinte_et_une_legende(self):
        self.assertIn('h_src === "defaut"', self.js)
        self.assertIn("bat_defaut", self.js)
        self.assertIn("updateTriggers", self.js)
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="legende-hauteurs"', html)
        self.assertIn("Hauteur par défaut", html)

    def test_reponse_de_gare_perimee_jetee(self):
        self.assertIn("demandee !== gareChoisie", self.js)


if __name__ == "__main__":
    unittest.main()


class TestTableauRendu(unittest.TestCase):
    """Maquette `web/tableau-rendu.html` : un train par état, de près et de loin, en thème clair et sombre (données inventées, aucun serveur PRIM)."""
    ETATS = ("En route", "À quai", "Terminus", "Données anciennes", "Horaire théorique", "Train sélectionné", "Retenu")
    SANS_POSITION = ("Non placé", "Position inconnue", "Terminé")

    def setUp(self):
        self.html = (WEB / "tableau-rendu.html").read_text(encoding="utf-8")
        self.js = (WEB / "tableau-rendu.js").read_text(encoding="utf-8")

    def test_aucune_adresse_externe_ni_script_en_ligne(self):
        for nom, texte in (("html", self.html), ("js", self.js)):
            texte = re.sub(r"/\*.*?\*/", "", texte, flags=re.S)
            texte = "\n".join(l for l in texte.splitlines() if not l.lstrip().startswith("//"))
            self.assertIsNone(URL_EXTERNE.search(texte), nom)
        # la politique CSP du serveur n'autorise que script-src 'self' : aucun script écrit dans la page
        for balise in re.findall(r"<script\b[^>]*>", self.html):
            self.assertIn("src=", balise)
        self.assertIn("vendor/deck.gl-9.4.1.min.js", self.html)
        self.assertIn("modele-z8100.js", self.html)
        self.assertIn("tableau-rendu.js", self.html)

    def test_tous_les_etats_sont_montres(self):
        for e in self.ETATS:
            self.assertIn(e, self.js, e)
        for e in self.SANS_POSITION:
            self.assertIn(e, self.html + self.js, e)

    def test_les_deux_propositions_de_retenu_et_les_quatre_vues(self):
        self.assertIn("proposition A", self.js)
        self.assertIn("proposition B", self.js)
        for vue in ("clair-pres", "clair-loin", "sombre-pres", "sombre-loin"):
            self.assertIn(vue, self.html + self.js, vue)

    def test_distingue_l_existant_du_propose(self):
        self.assertIn("existe déjà", self.js)
        self.assertIn("proposé", self.js)

    def test_les_couleurs_d_etat_sont_celles_de_la_carte(self):
        carte = (WEB / "carte.js").read_text(encoding="utf-8")
        for nom in ("COULEURS", "GRIS", "ROSE_ANCIENNES", "ROUGE_TERMINUS"):
            valeur = re.search(rf"const {nom} = (\{{[^}}]*\}}|\[[^\]]*\]);", carte).group(1)
            self.assertIn(valeur, self.js, nom)

    def test_contour_autour_de_la_rame_option_du_tableau(self):
        # proposé le 18/11 : contour en gélule posé au sol autour de la rame (un tracé par état, épaisseur en pixels), au lieu d'un cercle dessous
        self.assertIn('id="contour"', self.html)
        self.assertIn("Contour en gélule", self.html)
        self.assertIn("PathLayer", self.js)
        self.assertIn('widthUnits: "pixels"', self.js)
        self.assertNotIn("cullMode", self.js)   # la coque 3D retournée est abandonnée

    def test_camera_pilotable_dans_le_tableau(self):
        # 18/11 : Mathis veut tourner autour des rames pour juger le contour en 3D
        self.assertIn("controller: true", self.js)
        self.assertIn("dragRotate: true", self.js)
        self.assertIn("maxPitch: 85", self.js)
        for bouton in ("vue-reinit", "vue-dessus", "vue-cote", "vue-bout", "vue-tourner", "sync"):
            self.assertIn(f'id="{bouton}"', self.html)

    def test_detaille_des_le_zoom_15_et_icone_en_dessous(self):
        # 18/11 : seuil à 15 décidé par Mathis (12 essayé : détaillé agrandi moins lisible que l'icône) ; l'icône (palette alignée) ne sert qu'en dessous
        self.assertRegex(self.js, r"const ZOOM_DETAIL = 15,")
        self.assertIn("ZOOM_PLEIN = 16", self.js)
        self.assertIn("PALETTE_ALIGNEE", self.js)
        self.assertIn('id="palette"', self.html)
        for vue in ("clair-moyen", "sombre-moyen"):
            self.assertIn(vue, self.html + self.js, vue)

    def test_page_de_comparaison_avant_apres_par_niveau_de_zoom(self):
        html = (WEB / "comparaison-zoom.html").read_text(encoding="utf-8")
        js = (WEB / "comparaison-zoom.js").read_text(encoding="utf-8")
        for balise in re.findall(r"<script\b[^>]*>", html):
            self.assertIn("src=", balise)   # CSP du serveur : aucun script en ligne
        for nom, texte in (("html", html), ("js", js)):
            texte = "\n".join(l for l in texte.splitlines() if not l.lstrip().startswith("//"))
            self.assertIsNone(URL_EXTERNE.search(texte), nom)
        self.assertIn("tableau-rendu.js", html)
        self.assertIn("ZOOMS = [10, 11, 12, 13, 14, 15, 16, 17]", js)   # un niveau de zoom de plus = échelle x2
        self.assertIn("AVANT", html + js)
        self.assertIn("APRÈS", html + js)

    def test_cumuls_d_etats_montres(self):
        self.assertIn("Terminus + sélectionné", self.js)
        self.assertIn("Retenu B + données anciennes", self.js)
