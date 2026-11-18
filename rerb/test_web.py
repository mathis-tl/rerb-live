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
        self.assertNotIn("flatMap", self.js.split("function calquesDecor")[1].split("\n}\n")[0], "pas de fusion de toutes les cellules")
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
        self.assertIn("données anciennes", html)
        self.assertIn("horaire théorique", html)
        self.assertIn("pas de nouvelle information", self.js)
        self.assertIn("n'annonce aucun écart", self.js)
        # les notes ne prétendent pas plus que ce qu'on sait (décision du 18/11, piste A)
        self.assertIn("extrapolée à partir de la dernière estimation", self.js)
        self.assertNotIn("vient de l'horaire seul", self.js)
        self.assertIn("soit le train est à l'heure, soit PRIM ne suit pas ce train en temps réel", self.js)
        self.assertIn("soit le train est à l'heure, soit PRIM ne suit pas ce train en temps réel", html)

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
        self.assertIn("soit PRIM ne suit pas ce train en temps réel", e["note"])
        deux = self._etiquettes({"donnees_anciennes": True, "horaire_theorique": True})
        self.assertEqual([x["libelle"] for x in deux], ["Données anciennes", "Horaire théorique"])

    def test_couleur_grise_seulement_pour_des_donnees_anciennes(self):
        c = lambda t: self._node("// --- Couleur et orientation", "// --- etiquettes",
                                 f"(globalThis.GRIS = 'gris', globalThis.ROUGE_TERMINUS = 'rouge', globalThis.COULEURS = {{'en route': 'bleu'}}, couleurTrain({json.dumps(t)}))")
        gris = c({"donnees_anciennes": True, "etat": "en route"})
        self.assertEqual(c({"horaire_theorique": True, "etat": "en route"}), c({"etat": "en route"}), "horaire théorique : pas de couleur d'alerte")
        self.assertNotEqual(gris, c({"etat": "en route"}))

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
        for nom in ("COULEURS", "GRIS", "ROUGE_TERMINUS"):
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
