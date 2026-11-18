// Tableau de rendu des états (maquette, données INVENTÉES) : un train par état, de près et de loin, en thème clair et sombre.
// Reprend les constantes, l'échelle et les modèles 3D de web/carte.js ; les rendus « proposé » n'existent pas encore dans la carte.
"use strict";

// --- couleurs d'état (copie de web/carte.js ; un test vérifie qu'elles restent identiques)
const COULEURS = { "à quai": [47, 133, 90], "en route": [43, 108, 176] };
const GRIS = [160, 167, 173];
const ROUGE_TERMINUS = [226, 34, 48];
const AMBRE = [245, 158, 11];     // anneau du train sélectionné (existe déjà)
const JAUNE = [250, 204, 21];     // proposé pour « retenu »
const COULEUR_NOM = { clair: { texte: [20, 30, 40, 255], contour: [255, 255, 255, 245] }, sombre: { texte: [255, 255, 255, 255], contour: [14, 20, 30, 235] } };

// statut : "existe déjà" = code de la carte actuelle ; "proposé" = idée à juger
const LIGNES = [
  { cle: "en-route", court: "En route", etat: "en route", statut: "existe déjà",
    note: "Disque bleu sous l'icône de loin. De près, le modèle détaillé n'a aucun disque : rien n'indique l'état." },
  { cle: "a-quai", court: "À quai", etat: "à quai", statut: "existe déjà", note: "Disque vert de loin. Rien de près." },
  { cle: "terminus", court: "Terminus", etat: "à quai", terminus: true, statut: "existe déjà",
    note: "Rame arrivée au terminus (séjour de 60 s avant de disparaître) : disque rouge et halo rouge brillant qui pulse, de près comme de loin." },
  { cle: "anciennes", court: "Données anciennes", etat: "en route", anciennes: true, statut: "existe déjà",
    note: "Aucune nouvelle information de PRIM depuis plus de 15 min : disque gris de loin. De près, rien ne le distingue." },
  { cle: "theorique", court: "Horaire théorique", etat: "en route", theorique: true, statut: "existe déjà",
    note: "Indicatif, sans couleur (décision du 18/11) : le train s'affiche comme « en route ». Le libellé n'apparaît que dans l'infobulle et la fiche." },
  { cle: "selection", court: "Train sélectionné", etat: "en route", selection: true, statut: "existe déjà",
    note: "Anneau ambre de 16 px de diamètre fixe, par-dessus l'état. De près, il est minuscule à côté de la rame (constat)." },
  { cle: "retenu-a", court: "Retenu, proposition A", etat: "en route", retenu: "A", statut: "proposé",
    note: "Train retenu (PRIM annonce plus de 120 s d'attente) : disque jaune et anneau jaune fixe de la taille de la rame. Jaune différent de l'ambre de la sélection." },
  { cle: "retenu-b", court: "Retenu, proposition B", etat: "en route", retenu: "B", statut: "proposé",
    note: "Disque d'état d'origine (bleu) et halo jaune qui pulse lentement (période plus longue que le rouge du terminus : se distingue aussi sans couleur)." },
  { cle: "cumul-1", court: "Terminus + sélectionné", etat: "à quai", terminus: true, selection: true, statut: "proposé",
    note: "Deux états à la fois : un contour par état, emboîtés. Le plus important (terminus) est au plus près de la rame, la sélection autour." },
  { cle: "cumul-2", court: "Retenu B + données anciennes", etat: "en route", retenu: "B", anciennes: true, statut: "proposé",
    note: "Autre cumul : contour jaune qui pulse lentement, entouré du contour gris des données anciennes." },
];
const SANS_POSITION = [
  ["Non placé", "heures PRIM inutilisables (course annulée, horaires incohérents) : aucun train dessiné ; listé à part dans le panneau « sans position » de la carte, jamais masqué."],
  ["Position inconnue", "le serveur ne sait pas où est le train : aucun train dessiné ; listé à part dans le même panneau."],
  ["Terminé", "le séjour de 60 s au terminus est écoulé : le train disparaît de la carte, sans entrée dans la liste."],
];

// --- échelle du train (copie de web/carte.js)
// Décidé le 18/11 (Mathis) : le modèle détaillé s'affiche dès le zoom 15 (au lieu de 16), élargi progressivement entre le zoom 16 (taille réelle) et 15 (largeur
// cible en pixels, comme l'icône) ; en dessous de 15 on garde l'icône (le détaillé agrandi y était moins lisible : essai du seuil 12, comparaison-zoom.html).
const ZOOM_DETAIL = 15, ZOOM_PLEIN = 16, ZOOM_CIBLE = 15, CIBLE_RAME_PX = 40, CIBLE_LARGEUR_PX = 10, LONGUEUR_RAME_M = 104.05, LARGEUR_ICONE_M = 4.2, LARGEUR_CAISSE_M = 2.798;
const metresParPixel = zoom => 78271.517 * Math.cos(48.8 * Math.PI / 180) / Math.pow(2, zoom);
function echelleTrain(zoom, ancien = false) {   // ancien = comportement de la carte actuelle : détaillé à taille réelle dès le zoom 16, icône agrandie en dessous (sert à la page de comparaison)
  const borne = (v, max) => Math.min(max, Math.max(1, v)), mpp = metresParPixel(zoom);
  if (ancien && zoom >= ZOOM_PLEIN) return { detail: true, longueur: 1, largeur: 1 };
  if (!ancien && zoom >= ZOOM_DETAIL) {   // modèle détaillé : facteurs d'agrandissement en longueur et en largeur/hauteur (1 = taille réelle)
    const t = Math.min(1, Math.max(0, (ZOOM_PLEIN - zoom) / (ZOOM_PLEIN - ZOOM_CIBLE))), lisse = t * t * (3 - 2 * t);
    const cibleL = borne(CIBLE_RAME_PX * mpp / LONGUEUR_RAME_M, 60), cibleW = borne(CIBLE_LARGEUR_PX * mpp / LARGEUR_CAISSE_M, 120);
    return { detail: true, longueur: 1 + (cibleL - 1) * lisse, largeur: 1 + (cibleW - 1) * lisse };
  }
  return { detail: false, longueur: borne(CIBLE_RAME_PX * mpp / LONGUEUR_RAME_M, 60), largeur: borne(CIBLE_LARGEUR_PX * mpp / LARGEUR_ICONE_M, 120) };
}
const MODELES = { motrice: ModeleZ8100.construire({ voiture: 0 }), remorque: ModeleZ8100.construire({ voiture: 1 }), icone: ModeleZ8100.construire({ icone: true }) };
// Proposé : l'icône (zoom < 15) reprend la palette du modèle détaillé (bandeau de vitres bleu-violet au lieu du bleu clair, bas de caisse sombre du modèle détaillé).
const PALETTE_ALIGNEE = { bleu: [105, 128, 176], noir: [52, 56, 62] };
const CENTRES_VOITURES_M = [39.0125, 13, -13, -39.0125];

// --- vues
const LON0 = 2.35, LAT0 = 48.80, KX = 111320 * Math.cos(LAT0 * Math.PI / 180), KY = 110540;
const VUES = {   // zoom0 : zoom de départ ET de disposition des trains (fixe) ; le zoom courant, lui, suit la molette (voir `etats`)
  "clair-pres": { theme: "clair", zoom0: 16.1, pres: true }, "sombre-pres": { theme: "sombre", zoom0: 16.1, pres: true },
  "clair-loin": { theme: "clair", zoom0: 13.6, pres: false }, "sombre-loin": { theme: "sombre", zoom0: 13.6, pres: false },
  "clair-moyen": { theme: "clair", zoom0: 15.2, pres: false }, "sombre-moyen": { theme: "sombre", zoom0: 15.2, pres: false },
};
const reglages = () => ({ contour: document.getElementById("contour").checked, socle: document.getElementById("socle").checked, palette: document.getElementById("palette").checked, incline: document.getElementById("incline").checked, pulse: document.getElementById("pulse").checked });

const couleurEtat = l => (l.anciennes ? GRIS : l.terminus ? ROUGE_TERMINUS : l.retenu === "A" ? JAUNE : COULEURS[l.etat]);

function lignesPlacees(vue) {   // grille de 2 colonnes pour les deux échelles (près : les halos de 110 m ne se chevauchent pas ; loin : les icônes de 40 px non plus)
  const liste = vue.cles ? LIGNES.filter(l => vue.cles.includes(l.cle)) : LIGNES;
  const colonnes = vue.colonnes || 2, n = liste.length, rangees = Math.ceil(n / colonnes), mpp = metresParPixel(vue.zoom0);
  const dx = vue.pres ? 70 : Math.max(90 * mpp, vue.cles ? 80 : 0), dy = vue.pres ? 125 : (colonnes === 1 ? 85 : 130) * mpp;
  return liste.map((l, i) => ({ ...l, lon: LON0 + (colonnes === 1 ? 0 : i % 2 ? dx : -dx) / KX, lat: LAT0 + ((rangees - 1) / 2 - Math.floor(i / colonnes)) * dy / KY, couleur: couleurEtat(l) }));
}

function calques(vue, maintenant) {
  const r = vue.reglages || reglages(), L = [], donnees = lignesPlacees(vue), ech = echelleTrain(vue.zoom, !!vue.ancien);
  const matiere = { ambient: 0.55, diffuse: 0.65, shininess: 24, specularColor: [90, 90, 90] };
  const nom = COULEUR_NOM[vue.theme];
  if (ech.detail) {   // de près : quatre caisses par rame, posées sur une voie droite orientée vers l'est
    const caisses = donnees.flatMap(d => CENTRES_VOITURES_M.map((c, rang) => ({ ...d, lon: d.lon + c * ech.longueur / KX, rang })));
    if (r.socle) {   // proposé : un socle de la couleur de l'état sous la rame
      L.push(new deck.PolygonLayer({
        id: "socle", data: donnees, getPolygon: d => { const a = 54 * ech.longueur / KX, b = 3.2 * ech.largeur / KY; return [[d.lon - a, d.lat - b], [d.lon + a, d.lat - b], [d.lon + a, d.lat + b], [d.lon - a, d.lat + b]]; },
        getFillColor: d => [...d.couleur, 120], getLineColor: d => [...d.couleur, 255], stroked: true, lineWidthMinPixels: 2, parameters: { depthCompare: "always" },
      }));
    }
    for (const [type, est] of [["motrice", d => d.rang === 0 || d.rang === 3], ["remorque", d => d.rang === 1 || d.rang === 2]]) {
      const lot = caisses.filter(est);
      for (const piece of MODELES[type].pieces) {
        const vitre = piece.nom === "vitre";
        L.push(new deck.SimpleMeshLayer({
          id: `detail-${type}-${piece.nom}`, data: lot, mesh: piece.maillage, getPosition: d => [d.lon, d.lat, 0],
          getColor: [...piece.couleur, vitre ? 90 : 255], getOrientation: d => [0, d.rang >= 2 ? 180 : 0, 0], getScale: [ech.longueur, ech.largeur, ech.largeur], sizeScale: 1, material: matiere,
          parameters: vitre ? { depthWriteEnabled: false } : undefined,
        }));
      }
    }
  } else {   // de loin : icône rigide agrandie, disque coloré selon l'état
    for (const piece of MODELES.icone.pieces) {
      const vitre = piece.nom === "vitre", disque = piece.nom === "disque";
      L.push(new deck.SimpleMeshLayer({
        id: `icone-${piece.nom}`, data: donnees, mesh: piece.maillage, getPosition: d => [d.lon, d.lat, 0],
        getColor: d => (disque ? [...d.couleur, 255] : [...(r.palette && PALETTE_ALIGNEE[piece.nom] || piece.couleur), vitre ? 90 : 255]), getOrientation: [0, 0, 0],
        updateTriggers: { getColor: [r.palette] },
        getScale: [ech.longueur, ech.largeur, ech.largeur], sizeScale: 1, material: matiere,
        parameters: vitre ? { depthWriteEnabled: false } : undefined,
      }));
    }
  }
  const phase = (periode) => (r.pulse ? 0.5 + 0.5 * Math.sin(maintenant / periode) : 0.5);
  const p1 = phase(280);
  const p2 = phase(700);
  if (r.contour) L.push(...calquesContour(donnees, vue, ech, p1, p2));
  const terminus = r.contour ? [] : donnees.filter(d => d.terminus);   // halo rouge du terminus (existe déjà : copie du code de la carte)
  L.push(new deck.ScatterplotLayer({
    id: "terminus-halo", data: terminus, getPosition: d => [d.lon, d.lat], getRadius: 55, radiusUnits: "meters", radiusMinPixels: 16,
    filled: true, stroked: true, getFillColor: [...ROUGE_TERMINUS, Math.round(40 + 50 * p1)], getLineColor: [255, 60, 75, Math.round(170 + 85 * p1)],
    lineWidthMinPixels: 3, parameters: { depthCompare: "always" },
  }));
  L.push(new deck.ScatterplotLayer({   // proposé : anneau jaune fixe (A)
    id: "retenu-a", data: r.contour ? [] : donnees.filter(d => d.retenu === "A"), getPosition: d => [d.lon, d.lat], getRadius: 55, radiusUnits: "meters", radiusMinPixels: 16,
    filled: false, stroked: true, getLineColor: [...JAUNE, 255], lineWidthMinPixels: 3, parameters: { depthCompare: "always" },
  }));
  L.push(new deck.ScatterplotLayer({   // proposé : halo jaune lent (B)
    id: "retenu-b", data: r.contour ? [] : donnees.filter(d => d.retenu === "B"), getPosition: d => [d.lon, d.lat], getRadius: 55, radiusUnits: "meters", radiusMinPixels: 16,
    filled: true, stroked: true, getFillColor: [...JAUNE, Math.round(30 + 60 * p2)], getLineColor: [...JAUNE, Math.round(150 + 105 * p2)],
    lineWidthMinPixels: 3, parameters: { depthCompare: "always" },
  }));
  L.push(new deck.ScatterplotLayer({   // anneau ambre de sélection (existe déjà : 16 px fixes)
    id: "selection", data: r.contour ? [] : donnees.filter(d => d.selection), getPosition: d => [d.lon, d.lat], getRadius: 1, radiusMinPixels: 16, radiusMaxPixels: 16,
    filled: false, stroked: true, getLineColor: AMBRE, lineWidthMinPixels: 3, parameters: { depthCompare: "always" },
  }));
  L.push(new deck.TextLayer({
    id: "noms", data: donnees, getPosition: d => [d.lon, d.lat], getText: d => d.court, getSize: 13, sizeUnits: "pixels", getColor: nom.texte,
    characterSet: "auto", fontFamily: "system-ui, sans-serif", fontWeight: 600, getTextAnchor: "middle", getAlignmentBaseline: "top", getPixelOffset: [0, vue.pres ? 30 : 26],
    background: false, outlineWidth: 4, outlineColor: nom.contour, fontSettings: { sdf: true }, parameters: { depthCompare: "always" },
  }));
  return L;
}

// --- contour (proposé) : une « gélule » (rectangle aux bouts arrondis) posée au sol autour de l'emprise de la rame, tracée avec une épaisseur en PIXELS constante.
// Un seul tracé par rame : pas de chevauchement entre caisses, même rendu de près (modèle détaillé) et de loin (icône agrandie). Plusieurs états = plusieurs
// gélules emboîtées, l'état le plus important au plus près de la rame.
const ROUGE_VIF = [255, 60, 75];
const PRIORITE = [   // du plus important (au plus près de la rame) au moins important
  { champ: "terminus", couleur: ROUGE_VIF, pulse: "rapide" }, { champ: "retenu", couleur: JAUNE, pulse: "lent" },
  { champ: "selection", couleur: AMBRE }, { champ: "anciennes", couleur: GRIS },
];
const contoursDe = d => PRIORITE.filter(c => d[c.champ]).map(c => (c.champ === "retenu" && d.retenu === "A" ? { ...c, pulse: undefined } : c));
const PX_TRAIT = 2.4, PX_ECART = 3.5, PX_PULSE = 1.6;   // épaisseur du trait, écart rame -> 1er trait (et entre traits), amplitude du pulse (épaisseur)
const DEMI_LARGEUR_DETAIL_M = 1.45;

function gelule(demiLong, demiLarg, marge, pasAngle = 12) {   // contour fermé en mètres [x, y] : demi-cercles aux deux bouts
  const r = demiLarg + marge, c = Math.max(0, demiLong + marge - r), pts = [];
  for (let a = -90; a <= 90; a += pasAngle) pts.push([c + r * Math.cos(a * Math.PI / 180), r * Math.sin(a * Math.PI / 180)]);
  for (let a = 90; a <= 270; a += pasAngle) pts.push([-c + r * Math.cos(a * Math.PI / 180), r * Math.sin(a * Math.PI / 180)]);
  pts.push(pts[0]);
  return pts;
}

function calquesContour(donnees, vue, ech, p1, p2) {
  const mpp = metresParPixel(vue.zoom), L = [];
  const demiLong = LONGUEUR_RAME_M / 2 * ech.longueur;
  const demiLarg = ech.detail ? DEMI_LARGEUR_DETAIL_M * ech.largeur : LARGEUR_ICONE_M / 2 * ech.largeur;
  const hauteur = ech.detail ? 0.3 : 0.4 * mpp;   // juste au-dessus du sol / du disque d'état de l'icône
  const traits = [];
  for (const d of donnees) contoursDe(d).forEach((c, rang) => {
    const marge = (PX_ECART + rang * (PX_TRAIT + PX_ECART)) * mpp;
    const phase = c.pulse === "rapide" ? p1 : c.pulse === "lent" ? p2 : 0;
    traits.push({ chemin: gelule(demiLong, demiLarg, marge).map(([x, y]) => [d.lon + x / KX, d.lat + y / KY, hauteur]), couleur: c.couleur, largeur: PX_TRAIT + PX_PULSE * phase });
  });
  L.push(new deck.PathLayer({
    id: "contour-gelule", data: traits, getPath: t => t.chemin, getColor: t => t.couleur, getWidth: t => t.largeur, widthUnits: "pixels", jointRounded: true, capRounded: true,
  }));
  return L;
}

if (document.getElementById("grille")) (function () {
// --- page : quatre vues indépendantes et pilotables (glisser = déplacer ; Ctrl + glisser, clic droit + glisser ou Maj + glisser = pivoter et incliner ; molette = zoom)
const decks = {}, etats = {};
const etatDepart = vue => ({ longitude: LON0, latitude: LAT0, zoom: vue.zoom0, pitch: reglages().incline ? 55 : 0, bearing: 0, minZoom: 11, maxZoom: 21, maxPitch: 85 });
function reinitialiser(pitch, bearing) {
  for (const [id, vue] of Object.entries(VUES)) etats[id] = { ...etatDepart(vue), ...(pitch === undefined ? {} : { pitch, bearing }) };
}
function creer() {
  reinitialiser();
  for (const [id] of Object.entries(VUES)) {
    decks[id] = new deck.Deck({
      parent: document.getElementById(id), views: new deck.MapView({ id: "carte", controller: { dragRotate: true, touchRotate: true, keyboard: false } }), controller: true,
      viewState: etats[id], layers: [],
      onViewStateChange: ({ viewState }) => {
        etats[id] = viewState;
        if (document.getElementById("sync").checked) for (const autre of Object.keys(etats)) if (autre !== id) etats[autre] = { ...etats[autre], pitch: viewState.pitch, bearing: viewState.bearing };
      },
    });
  }
}
function dessiner(maintenant) {
  for (const [id, vue] of Object.entries(VUES)) decks[id].setProps({ viewState: etats[id], layers: calques({ ...vue, zoom: etats[id].zoom }, maintenant) });
}
function legende() {
  document.getElementById("legende").innerHTML = LIGNES.map(l => {
    const e = l.statut === "existe déjà";
    return `<div class="ligne ${e ? "existe" : "propose"}"><b>${l.court}<span class="etiquette ${e ? "e" : "p"}">${l.statut}</span></b>${l.note}</div>`;
  }).join("");
  document.getElementById("sans-position").innerHTML = SANS_POSITION.map(([n, t]) => `<div class="ligne existe" style="margin-bottom:6px"><b>${n}</b>${t}</div>`).join("");
}
legende();
creer();
document.getElementById("incline").addEventListener("change", () => reinitialiser());
document.getElementById("vue-reinit").addEventListener("click", () => reinitialiser());
document.getElementById("vue-dessus").addEventListener("click", () => reinitialiser(0, 0));
document.getElementById("vue-cote").addEventListener("click", () => reinitialiser(80, 0));
document.getElementById("vue-bout").addEventListener("click", () => reinitialiser(70, 90));
document.getElementById("vue-tourner").addEventListener("click", () => { for (const id of Object.keys(etats)) etats[id] = { ...etats[id], bearing: (etats[id].bearing + 45) % 360 }; });
(function boucle(maintenant) { dessiner(maintenant); requestAnimationFrame(boucle); })(performance.now());
})();
