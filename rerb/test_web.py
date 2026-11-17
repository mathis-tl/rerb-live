"""Tâche 4, J4 : la page de la carte n'appelle rien hors du serveur local (garde-fou statique, sans navigateur).

Complète la politique CSP envoyée par le serveur (test_serveur.py) et la garde réseau de carte.js : si quelqu'un
ajoute un CDN, une tuile ou une police en ligne dans la page, ce test échoue. Le fichier deck.gl vendu
(web/vendor) est exclu : son contenu est audité dans web/vendor/VERSIONS.md.
"""
import json
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
        self.assertEqual(self.js.count("placesAffiches("), 3, "définie une fois, utilisée dans calques() et pour retrouver un train à suivre")

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

    def test_bascule_icone_detail_au_zoom_16(self):
        self.assertTrue(self._echelle(16)["detail"])
        self.assertFalse(self._echelle(15.9)["detail"])

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
        self.assertIn('mode === "zone"', corps.split('id: "pastilles"')[0][-120:])
        self.assertIn('id: "bulles"', corps)
        self.assertIn("calquesTrains(", corps)

    def test_zoom_redessine_les_trains_et_legende(self):
        self.assertIn("pasZoom", self.js)
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("agrandi", html)
        self.assertIn('name="direction"', html)

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

    def test_deplacer_en_glissant_et_tourner_au_clic_droit_ou_maj(self):
        self.assertIn('dragMode: modeGlisser', self.js)
        self.assertIn('modeGlisser = "pan"', self.js, "par défaut le glisser déplace (Google Maps, Apple Plans)")
        self.assertIn('e.key === "Shift"', self.js, "Maj enfoncée : tourner")
        self.assertIn("inertia", self.js)
        html = (WEB / "index.html").read_text(encoding="utf-8")
        for ident in ("mode-glisser", "zoom-plus", "zoom-moins", "boussole-carte", "incliner-carte"):
            self.assertIn(f'id="{ident}"', html, ident)

    def test_zoom_molette_ne_coupe_pas_le_suivi(self):
        # deck.gl annonce isPanning aussi pendant un zoom à la molette (le point sous la souris bouge) : seul un vrai glisser arrête le suivi
        self.assertIn("i.isDragging && i.isPanning && !i.isZooming", self.js)

    def test_decor_par_cellule_pas_de_refonte_globale(self):
        # avant : toutes les cellules fusionnées en un seul jeu de données, tout recalculé à chaque cellule arrivée (saccades au déplacement)
        self.assertIn('id: "surfaces-" + k', self.js)
        self.assertIn('id: "batiments-" + k', self.js)
        self.assertNotIn("flatMap", self.js)
        self.assertIn("cellulesAMontrer", self.js, "une seule nouvelle cellule par image")

    def test_clic_gare_zoome_clic_train_suit_et_affiche_la_fiche(self):
        corps = self.js.split("onClick: ({ object, layer })")[1].split("});\n}")[0]
        self.assertIn('layer.id === "noms-gares"', corps)
        self.assertIn("allerGare(object)", corps)
        self.assertIn("suivreTrain(object.course)", corps)
        self.assertIn("pickable: true", self.js.split('id: "noms-gares"')[1].split("}));")[0], "le nom de la gare est cliquable")
        html = (WEB / "index.html").read_text(encoding="utf-8")
        for ident in ("fiche-train", "fiche-corps", "fiche-suivi", "fiche-fermer"):
            self.assertIn(f'id="{ident}"', html, ident)

    def test_une_seule_direction_a_la_fois_avec_bouton_et_texte_sur_la_carte(self):
        self.assertIn('filtreDirection = "nord"', self.js)
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="direction-texte"', html)
        self.assertIn('id="direction-changer"', html)
        self.assertNotIn('value="tout"', html)
        self.assertIn("Trains affichés : ", self.js)

    def test_train_au_terminus_rouge_avec_halo(self):
        self.assertIn("t.terminus ? ROUGE_TERMINUS", self.js)
        self.assertIn('id: "terminus-halo"', self.js)
        self.assertIn("trains.some(t => t.terminus)", self.js, "le halo pulse : l'animation continue tant qu'un train est au terminus")

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

    def test_peremption_expliquee(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("15 min", html)
        self.assertIn("périmé", html)
        self.assertIn("pas de nouvelle information", self.js)

    def test_batiments_a_hauteur_par_defaut_ont_une_teinte_et_une_legende(self):
        self.assertIn('h_src === "defaut"', self.js)
        self.assertIn("bat_defaut", self.js)
        self.assertIn("updateTriggers", self.js)
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="legende-hauteurs"', html)
        self.assertIn("hauteur par défaut", html)

    def test_reponse_de_gare_perimee_jetee(self):
        self.assertIn("demandee !== gareChoisie", self.js)


if __name__ == "__main__":
    unittest.main()
