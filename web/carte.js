// Carte 3D du RER B (J4). deck.gl 9.4.1 servi en local, AUCUN fond de carte, AUCUNE requête hors de ce serveur.
// Données : /geo (tracé, gares) et /trains (position estimée recalculée par le serveur à chaque requête).
"use strict";

// --- Garde réseau (en plus de la politique CSP envoyée par le serveur) : aucune requête vers un autre site.
const fetchOrigine = window.fetch.bind(window);
window.fetch = (url, options) => {
  const cible = new URL(url, location.href);
  if (cible.origin !== location.origin) return Promise.reject(new Error("requête externe bloquée : " + cible.origin));
  return fetchOrigine(url, options);
};

const INTERVALLE_MS = 3000;            // le serveur n'appelle PRIM qu'au plus toutes les 120 s : interroger plus souvent ne coûte rien

// --- chargement
// Le serveur n'appelle PRIM que lorsqu'une page demande /trains : la page ne doit donc interroger que lorsqu'elle est regardée.
const DELAI_LENT_MS = 8000;             // au-delà, on explique que PRIM est lent
const DELAI_REQUETE_MS = 45000;         // le serveur attend PRIM jusqu'à 30 s : au-delà de 45 s, la requête est abandonnée
const SEUIL_MAJ_S = 120;                // relevé plus vieux que l'intervalle serveur : le prochain /trains rappelle PRIM
// Écran de chargement (avant les premières données). null = rien à montrer.
function etatChargement({ recu, attenteMs, erreur, bloque, reessai }) {
  if (recu) return null;
  if (bloque) return { type: "bloque", reessayer: false, message: "PRIM refuse les appels (clé refusée ou quota atteint) : aucun train à afficher pour l'instant." };
  if (erreur && !reessai) return { type: "echec", reessayer: true, message: "Les trains n'ont pas pu être chargés (" + erreur + ")." };
  return attenteMs >= DELAI_LENT_MS
    ? { type: "lent", reessayer: false, message: "PRIM met du temps à répondre… les trains arrivent." }
    : { type: "attente", reessayer: false, message: "Chargement des trains du RER B…" };
}
// Pastille discrète une fois les premières données reçues. null = rien à montrer.
function etatDonnees({ enCours, ageS, erreur, bloque, reveil }) {
  if (bloque) return { type: "bloque", message: "PRIM ne répond plus (clé refusée ou quota) : positions du dernier relevé." };
  if (erreur) return { type: "erreur", message: "Connexion perdue : positions du dernier relevé reçu." };
  if (enCours && (reveil || ageS >= SEUIL_MAJ_S)) return { type: "maj", message: "Mise à jour des trains…" };
  return null;
}
function interrogationActive(cache) { return !cache; }
// --- fin chargement

// --- accueil
// Carte de présentation au-dessus de la carte : titre, phrase, état du chargement, bouton « Voir les trains ».
// vu : déjà présentée dans cette session (on ne garde alors que l'état du chargement, qui disparaît seul) ; recu : premières données reçues ;
// ferme : le visiteur a cliqué sur « Voir les trains ».
function etatAccueil({ vu, recu, ferme }) {
  if (ferme) return { afficher: false, compact: false, bouton: false };
  if (vu) return { afficher: !recu, compact: true, bouton: false };
  return { afficher: true, compact: false, bouton: recu };
}
// --- fin accueil
const COULEURS = { "à quai": [47, 133, 90], "en route": [43, 108, 176] };
const ROUGE_TERMINUS = [226, 34, 48];   // train arrivé au terminus (séjour de 60 s avant de disparaître)
const GRIS = [160, 167, 173];   // repli d'un état inconnu seulement
const ROSE_ANCIENNES = [236, 72, 153];   // données anciennes : le gris se voyait trop peu (21/11)
const JAUNE_RETENU = [250, 204, 21];   // train retenu en pleine voie (PRIM a repoussé son arrivée de plus de 120 s)
const COULEUR_SELECTION = [168, 85, 247];   // contour du train sélectionné (violet : l'ambre de l'ancien anneau se confondait avec le jaune prévu pour « retenu »)
const MASSY = { longitude: 2.2588, latitude: 48.7255 };

let geo = null, trains = [], statut = null, mode = "mixte", zoom = 9, pasZoom = 90, filtreDirection = "nord", groupeOuvert = null, groupes = new Map(), selection = null, gareChoisie = "58774", dernierePrevision = null;

// --- Modèles 3D de la rame Z 8100 (web/modele-z8100.js, cotes de la fiche) : détaillé de près, « icône » (style Waze / Google Maps) de loin.
// De près : deux maillages de caisse (motrice, remorque), posés chacun sur la voie ; la rame se plie dans les courbes. De loin : l'icône, rigide.
const MODELES = { motrice: ModeleZ8100.construire({ voiture: 0 }), remorque: ModeleZ8100.construire({ voiture: 1 }), icone: ModeleZ8100.construire({ icone: true }) };

// --- échelle du train
// De près (zoom >= ZOOM_PLEIN) : modèle détaillé à la taille réelle. Entre ZOOM_PLEIN et ZOOM_DETAIL (15) : le même modèle, élargi progressivement jusqu'à ~10 px de large
// (la longueur, déjà > 40 px, reste vraie). En dessous de ZOOM_DETAIL : modèle icône (rame élargie et rehaussée une fois pour toutes), agrandi en plus selon le zoom pour rester
// visible : ~40 px de long et ~10 px de large (rame 25 fois plus longue que large : un agrandissement uniforme resterait un fil). Seuil à 15 décidé le 18/11 : le détaillé
// agrandi aux zooms 12 à 14 était moins lisible que l'icône (page de comparaison). Mètres par pixel de deck.gl (tuiles de 512 px) à la latitude de la ligne (~48,8°).
// Taille AGRANDIE de loin : pas à l'échelle.
const ZOOM_DETAIL = 15, ZOOM_PLEIN = 16, CIBLE_RAME_PX = 40, CIBLE_LARGEUR_PX = 10;
const LONGUEUR_RAME_M = 104.05, LARGEUR_ICONE_M = 4.2, LARGEUR_CAISSE_M = 2.798;
// Palette de l'icône alignée sur le modèle détaillé (bandeau de vitres bleu-violet, bas de caisse sombre) pour que le passage au zoom 15 se voie peu.
const PALETTE_ICONE = { bleu: [84, 100, 146], noir: [52, 56, 62], blanc: [208, 214, 222] };
function echelleTrain(zoom) {
  const metresParPixel = 78271.517 * Math.cos(48.8 * Math.PI / 180) / Math.pow(2, zoom);
  const borne = (v, max) => Math.min(max, Math.max(1, v));
  if (zoom >= ZOOM_PLEIN) return { detail: true, longueur: 1, largeur: 1 };
  if (zoom >= ZOOM_DETAIL) {   // détaillé élargi, de façon continue (lissage de 0 à 1 entre les deux zooms)
    const t = (ZOOM_PLEIN - zoom) / (ZOOM_PLEIN - ZOOM_DETAIL), lisse = t * t * (3 - 2 * t);
    return { detail: true, longueur: 1, largeur: 1 + (borne(CIBLE_LARGEUR_PX * metresParPixel / LARGEUR_CAISSE_M, 120) - 1) * lisse };
  }
  return { detail: false, longueur: borne(CIBLE_RAME_PX * metresParPixel / LONGUEUR_RAME_M, 60), largeur: borne(CIBLE_LARGEUR_PX * metresParPixel / LARGEUR_ICONE_M, 120) };
}
// --- fin échelle du train

// --- direction
// Sens de marche d'après le cap (degrés, 0 = nord) : la ligne va globalement du sud (Saint-Rémy) au nord (CDG, Mitry). Cap inconnu : jamais masqué.
const directionTrain = t => (t.cap == null ? null : (t.cap < 90 || t.cap >= 270 ? "nord" : "sud"));
const garderTrain = (t, filtre) => filtre === "tout" || directionTrain(t) === null || directionTrain(t) === filtre;
// Une direction à la fois (bouton sur la carte) : pour l'instant les deux sens ne sont pas affichés ensemble.
const DIRECTIONS = { nord: { titre: "vers le NORD", detail: "Mitry-Claye · Aéroport CDG", autre: "sud" }, sud: { titre: "vers le SUD", detail: "Massy · Saint-Rémy · Robinson", autre: "nord" } };
// --- fin direction

// --- regroupement
// Trains proches À L'ÉCRAN : un seul reste visible (le « chef » = plus petit identifiant), les autres s'effacent EN CONTINU entre
// R_FUSION_PX (invisibles) et R_SEPARE_PX (entièrement visibles) ; la bulle « n » apparaît en sens inverse. Aucun saut quand ils s'écartent.
const R_FUSION_PX = 14, R_SEPARE_PX = 30;
function regrouper(points, selection) {
  const tries = [...points].sort((a, b) => (a.course < b.course ? -1 : a.course > b.course ? 1 : 0));
  const chefs = [], groupes = new Map(), alpha = {};
  for (const t of tries) {
    let chef = null, dmin = Infinity;
    for (const c of chefs) {
      const d = Math.hypot(t.x - c.x, t.y - c.y);
      if (d < R_SEPARE_PX && d < dmin) { chef = c; dmin = d; }
    }
    if (!chef) { chefs.push(t); alpha[t.course] = 1; groupes.set(t.course, { chef: t.course, x: t.x, y: t.y, membres: [t.course], fondus: 0, opacite: 0 }); continue; }
    const a = Math.min(1, Math.max(0, (dmin - R_FUSION_PX) / (R_SEPARE_PX - R_FUSION_PX)));
    alpha[t.course] = t.course === selection ? 1 : a;   // le train choisi n'est jamais masqué
    const g = groupes.get(chef.course);
    g.membres.push(t.course);
    if (a < 1) { g.fondus += 1; g.opacite = Math.max(g.opacite, 1 - a); }
  }
  const bulles = [...groupes.values()].filter(g => g.fondus > 0).map(g => ({ chef: g.chef, x: g.x, y: g.y, n: 1 + g.fondus, alpha: g.opacite, membres: g.membres }));
  return { alpha, bulles };
}
// --- fin regroupement

// --- recherche de gare
// Sans accents ni majuscules ni ponctuation : « chatelet » trouve « Châtelet - Les Halles ». Classement : début du nom, début d'un mot, contenu.
const normaliser = x => String(x == null ? "" : x).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
function suggestionsGares(gares, saisie, max = 8) {
  const q = normaliser(saisie);
  if (!q) return [];
  const notes = [];
  for (const g of gares) {
    const n = normaliser(g.nom);
    const note = n.startsWith(q) ? 0 : n.split(" ").some(m => m.startsWith(q)) ? 1 : n.includes(q) ? 2 : q.split(" ").every(m => n.includes(m)) ? 3 : -1;
    if (note >= 0) notes.push([note, n.length, g]);
  }
  return notes.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2].nom.localeCompare(b[2].nom, "fr")).slice(0, max).map(x => x[2]);
}
// --- fin recherche de gare

// --- Couleur et orientation
const couleurTrain = t => (t.donnees_anciennes ? ROSE_ANCIENNES : t.terminus ? ROUGE_TERMINUS : t.retenu ? JAUNE_RETENU : (COULEURS[t.etat] || GRIS));
// --- etiquettes
// Deux faits distincts, jamais confondus (l'ancien mot, trompeur, a été remplacé le 18/11) :
//  • « Données anciennes » : PRIM n'a pas mis à jour ce train depuis plus de 15 min (certain) ; position extrapolée de la dernière estimation ; couleur rose (le gris se voyait trop peu, 21/11) ;
//  • « Horaire théorique » : l'heure affichée est celle de l'horaire, PRIM n'annonce aucun écart (indicatif : un train à l'heure donne le même résultat) ; aucune couleur d'alerte.
const NOTE_DONNEES_ANCIENNES = "pas de nouvelle information de PRIM sur ce train depuis plus de 15 min ; sa position est extrapolée à partir de la dernière estimation connue.";
const NOTE_RETENU = "PRIM a repoussé l'arrivée de ce train à la prochaine gare de plus de 2 min : il attend en pleine voie, juste avant la gare, puis repart pour arriver à la nouvelle heure.";
const NOTE_HORAIRE_THEORIQUE = "l'heure affichée est celle de l'horaire ; PRIM n'annonce aucun écart à la prochaine gare : soit le train est à l'heure, soit PRIM ne suit pas ce train en temps réel.";
function etiquettes(t) {
  const liste = [];
  if (t.retenu) liste.push({ libelle: "Retenu en voie", note: NOTE_RETENU });
  if (t.donnees_anciennes) liste.push({ libelle: "Données anciennes", note: NOTE_DONNEES_ANCIENNES });
  if (t.horaire_theorique) liste.push({ libelle: "Horaire théorique", note: NOTE_HORAIRE_THEORIQUE });
  return liste;
}
const suffixeEtiquettes = t => etiquettes(t).map(e => " (" + e.libelle.toLowerCase() + ")").join("");
// --- fin etiquettes
// cap serveur : degrés, 0 = nord, sens horaire. Orientation deck.gl : [tangage, lacet, roulis], lacet antihoraire depuis l'est.
const orientation = t => [0, 90 - (t.cap == null ? 0 : t.cap), 0];

// --- vue 3D
// Rotation et inclinaison pilotables aux boutons (en plus du clic droit ou ⌃/⌘ + glisser) : cap modulo 360, inclinaison entre 0° (de dessus) et MAX_PITCH.
const MAX_PITCH = 85;
function pasVue(vue, dBearing, dPitch) {
  return { bearing: (((vue.bearing || 0) + dBearing) % 360 + 360) % 360, pitch: Math.min(MAX_PITCH, Math.max(0, (vue.pitch || 0) + dPitch)) };
}
// Glisser sur la boussole : horizontal = tourner, vertical (vers le haut) = se rapprocher de l'horizontale. Degrés par pixel parcouru.
const DEG_CAP_PAR_PX = 0.6, DEG_INCL_PAR_PX = 0.4;
function deltaVue(dx, dy) { return { dBearing: dx * DEG_CAP_PAR_PX, dPitch: -dy * DEG_INCL_PAR_PX }; }
// --- fin vue 3D

// --- hauteurs des bandes
// Bandes d'incertitude posées juste au-dessus du sol, chacune à sa hauteur (plans coplanaires = z-fighting), sous le plancher de la rame (0,6 m).
const Z_BANDE = { p90: 0.2, mediane: 0.35 };
// --- fin hauteurs des bandes

const placés = () => trains.filter(t => t.lat != null && t.lon != null);

// --- Animation PAR IDENTIFIANT de course (pas par rang : deck.gl apparie par rang, et la liste change d'ordre à chaque
// relevé, ce qui faisait glisser un train depuis la position d'un autre). Chaque train glisse de sa position affichée vers
// la nouvelle ; une correction de plus de 250 m (nouveau relevé PRIM) glisse plus lentement pour rester visible comme
// correction, au lieu d'un saut. Train nouveau : posé directement. Train disparu : retiré.
const affiches = new Map();     // course -> { de:[lon,lat,cap], vers:[lon,lat,cap], t0, duree }
const SEUIL_CORRECTION_M = 250, DUREE_CORRECTION_MAX_MS = 12000;
const metres = (a, b) => Math.hypot((b[0] - a[0]) * 74000, (b[1] - a[1]) * 111000);
// --- interpolation
function ecartAngle(a, b) { return ((b - a + 540) % 360) - 180; }

// Vitesse CONSTANTE sur tout le segment (pas de lissage « départ-arrivée » : il ralentissait à zéro à chaque relevé = avance, pause, avance).
function positionAffichee(e, maintenant) {
  const f = e.duree <= 0 ? 1 : Math.min(1, Math.max(0, (maintenant - e.t0) / e.duree));
  return [e.de[0] + (e.vers[0] - e.de[0]) * f, e.de[1] + (e.vers[1] - e.de[1]) * f, e.de[2] + ecartAngle(e.de[2], e.vers[2]) * f, f];
}

// Le serveur n'envoie pas de cap à quai au terminus : on garde le dernier connu (sinon la rame se tourne vers le nord).
const capOuPrecedent = (cap, precedent) => (cap != null ? cap : (precedent != null ? precedent : 0));
// --- fin interpolation

function majAffiches(maintenant) {
  const presents = new Set();
  placés().forEach(t => {
    presents.add(t.course);
    const e = affiches.get(t.course);
    const vers = [t.lon, t.lat, capOuPrecedent(t.cap, e ? e.vers[2] : null)];
    if (!e) { affiches.set(t.course, { de: vers, vers, t0: maintenant, duree: 0, voie: t.voie }); return; }
    const [lon, lat, cap] = positionAffichee(e, maintenant);
    const saut = metres([lon, lat], vers);
    e.de = [lon, lat, cap]; e.vers = vers; e.t0 = maintenant;
    if (t.voie) e.voie = t.voie;   // au terminus le serveur n'en envoie plus : on garde la dernière
    e.duree = saut > SEUIL_CORRECTION_M ? Math.min(DUREE_CORRECTION_MAX_MS, saut / 50 * 1000) : INTERVALLE_MS;
  });
  for (const c of [...affiches.keys()]) if (!presents.has(c)) affiches.delete(c);
}

// trains placés, avec la position affichée (interpolée) à la place de la position reçue
function placesAffiches(maintenant) {
  return placés().filter(t => garderTrain(t, filtreDirection)).map(t => {
    const e = affiches.get(t.course);
    if (!e) return t;
    const [lon, lat, cap] = positionAffichee(e, maintenant);
    return { ...t, lon, lat, cap, voie: e.voie || t.voie };
  });
}

let animation = null;
function animer() {
  if (animation) return;
  const pas = () => {
    animation = null;
    const maintenant = performance.now();
    etapeCamera(maintenant);   // vol de caméra ou suivi d'un train
    afficher(maintenant);
    if (vol || suivi || trains.some(t => t.terminus || t.retenu) || [...affiches.values()].some(e => maintenant - e.t0 < e.duree)) animation = requestAnimationFrame(pas);
  };
  animation = requestAnimationFrame(pas);
}

// --- Décor 3D (OpenStreetMap, fichier local web/data/decor-massy.json, absent = pas de décor). Thème = celui du système.
let decor = null;   // repli sans index : fichier unique de Massy, traité comme une seule cellule
const sombre = window.matchMedia("(prefers-color-scheme: dark)");
// --- palette du décor
const PALETTE = {
  clair:  { fond: "#dfe5e3", sol: [236, 233, 226], vert: [200, 223, 190], eau: [166, 205, 230], bat: [214, 214, 216], bat_defaut: [222, 204, 176], quai: [244, 190, 180, 150],
            petit: [255, 255, 255], moyen: [255, 255, 255], grand: [250, 232, 190], pieton: [215, 212, 205], rail: [50, 60, 72, 190] },
  sombre: { fond: "#161d28", sol: [30, 36, 48], vert: [28, 58, 48], eau: [24, 44, 86], bat: [44, 50, 66], bat_defaut: [74, 66, 56], quai: [92, 92, 150, 150],
            petit: [52, 60, 76], moyen: [62, 70, 88], grand: [86, 90, 108], pieton: [44, 50, 62], rail: [255, 255, 255, 135] },   // rails comme dans l'essai « scan » : trait fin qui tranche sur le sol
};
// --- fin palette du décor

// --- styles de carte
// Trois habillages de décor à l'essai, à côté de l'actuel (qui reste le défaut). Choix par l'adresse (?style=scan|transport|verre) ou par les boutons de la carte.
// Ils sont sombres par construction (la carte est sombre quel que soit le thème du système) ; le panneau de gauche n'est pas touché.
const STYLES = ["actuel", "scan", "transport", "verre"];
const styleDepuis = recherche => { const v = new URLSearchParams(recherche).get("style"); return STYLES.includes(v) ? v : "actuel"; };
const DECORS = {
  // A. carte de scan : noir, arêtes blanches fines, seul le réseau est en couleur
  scan: { fond: "#000000", sol: [0, 0, 0], vert: [3, 9, 7], eau: [2, 7, 16], bat: [0, 0, 0, 150], bat_defaut: [0, 0, 0, 150], quai: [255, 255, 255, 24],
          petit: [255, 255, 255, 40], moyen: [255, 255, 255, 64], grand: [255, 255, 255, 96], pieton: [255, 255, 255, 20], rail: [255, 255, 255, 120] },
  // B. sobre « transport » : fond uni, bâtiments pleins très peu contrastés, la ligne en couleur
  transport: { fond: "#0e131b", sol: [18, 23, 31], vert: [20, 38, 34], eau: [16, 30, 52], bat: [30, 36, 46], bat_defaut: [32, 37, 46], quai: [90, 100, 130, 120],
               petit: [40, 48, 60], moyen: [52, 60, 74], grand: [70, 78, 94], pieton: [34, 40, 50], rail: [150, 160, 176] },
  // C. verre et lumière : bâtiments translucides bleutés, halos sur les trains
  verre: { fond: "#0a1020", sol: [14, 20, 38], vert: [16, 40, 48], eau: [20, 50, 100], bat: [110, 150, 220, 64], bat_defaut: [110, 150, 220, 64], quai: [140, 120, 255, 90],
           petit: [40, 56, 90], moyen: [52, 70, 110], grand: [70, 90, 140], pieton: [36, 48, 80], rail: [130, 160, 220] },
};
// Réglages des bâtiments, de la voie de la ligne et des gares par style (null = rendu actuel inchangé).
const RENDU = {
  actuel: null,
  scan: { fil: [255, 255, 255, 200], material: false, translucide: true, trace: { couleur: [255, 255, 255, 235], largeur: 5, halo: [255, 255, 255, 38] } },
  transport: { fil: null, material: { ambient: 0.8, diffuse: 0.25, shininess: 2, specularColor: [20, 22, 28] }, trace: { couleur: [64, 150, 255, 255], largeur: 9, halo: null } },
  verre: { fil: [170, 205, 255, 150], material: { ambient: 0.5, diffuse: 0.7, shininess: 60, specularColor: [190, 210, 255] }, translucide: true,
           trace: { couleur: [120, 195, 255, 255], largeur: 7, halo: [90, 160, 255, 60] } },
};
// --- fin styles de carte
let styleCarte = styleDepuis(location.search);
const sombreActif = () => styleCarte !== "actuel" || sombre.matches;
const palette = () => (styleCarte !== "actuel" ? DECORS[styleCarte] : PALETTE[sombre.matches ? "sombre" : "clair"]);
function appliquerTheme() {
  document.getElementById("carte").style.background = (decor || indexDecor) ? palette().fond : "";
  document.documentElement.dataset.theme = sombreActif() ? "sombre" : "clair";
  document.documentElement.dataset.style = styleCarte;
}
// Décor par cellules (web/data/decor/index.json + <cellule>.json, ~1,5 km chacune), chargées selon la vue à partir de ZOOM_DECOR.
// Chaque cellule a ses propres couches (identifiant par cellule) : une cellule qui arrive ne redessine QUE elle, les autres gardent leur
// géométrie déjà calculée (avant : toutes les cellules étaient refondues en un seul jeu de données, tout était recalculé à chaque arrivée = saccades).
// Sans index : repli sur le fichier unique de Massy. Sans aucun fichier : pas de décor, la carte reste sans fond.
const ZOOM_DECOR = 12.5, CELLULES_MAX = 80, ZOOM_DEPART = 14;
let indexDecor = null, vueCarte = { longitude: 2.2588, latitude: 48.7255, zoom: 14, pitch: 55, bearing: 0 };
const cellulesDecor = new Map(), enChargement = new Set();   // cellule -> décor préparé (null = absente/erreur)
const LARGEUR_ROUTE = { grand: 12, moyen: 9, petit: 6, pieton: 2.5 };

function preparerCellule(d) {   // regroupe en 3 jeux de données par cellule (surfaces, lignes, bâtiments) une seule fois, au chargement
  return {
    surfaces: [...d.verts.map(p => ({ p, t: "vert" })), ...d.eaux.map(p => ({ p, t: "eau" })), ...d.quais.map(p => ({ p, t: "quai" }))],
    lignes: [...d.cours_eau.map(c => ({ c, t: "eau", w: 6 })), ...d.routes.map(r => ({ c: r.c, t: r.t, w: LARGEUR_ROUTE[r.t] }))],
    rails: d.rails.map(c => ({ c, t: "rail", w: 1.2 })),   // calque à part : visible de loin (au moins 1 px), sans épaissir les routes
    batiments: d.batiments,
  };
}

function clesVisibles() {
  if (!indexDecor || vueCarte.zoom < ZOOM_DECOR) return [];
  const t = indexDecor.taille_deg, demi = 360 / (512 * 2 ** vueCarte.zoom) * Math.max(innerWidth, innerHeight) / 2 * 2.2;
  const existe = new Set(indexDecor.cellules), cles = [];
  for (let x = Math.floor((vueCarte.longitude - demi) / t); x <= Math.floor((vueCarte.longitude + demi) / t); x++)
    for (let y = Math.floor((vueCarte.latitude - demi * 0.7) / t); y <= Math.floor((vueCarte.latitude + demi * 0.7) / t); y++)
      if (existe.has(x + "_" + y)) cles.push(x + "_" + y);
  return cles.slice(0, CELLULES_MAX);
}
const clesPretes = () => clesVisibles().filter(k => cellulesDecor.get(k));

async function majDecor() {
  const cles = clesVisibles();
  const manquantes = cles.filter(k => !cellulesDecor.has(k) && !enChargement.has(k)).slice(0, 6);
  if (!manquantes.length) return;
  await Promise.all(manquantes.map(async k => {
    enChargement.add(k);
    try { const r = await fetch("/data/decor/" + k + ".json"); cellulesDecor.set(k, r.ok ? preparerCellule(await r.json()) : null); }
    catch (e) { /* réessayé au prochain déplacement */ }
    enChargement.delete(k);
  }));
  afficher();
  if (clesVisibles().some(k => !cellulesDecor.has(k) && !enChargement.has(k))) majDecor();
}

async function chargerDecor() {
  try {
    const ri = await fetch("/data/decor/index.json");
    if (ri.ok) {
      indexDecor = await ri.json();
      $("source-decor").textContent = indexDecor.source; $("source-decor").hidden = false;
      majDecor();
    } else {
      const r = await fetch("/data/decor-massy.json");
      if (r.ok) { const d = await r.json(); decor = preparerCellule(d); $("source-decor").textContent = d.source; $("source-decor").hidden = false; }
    }
  } catch (e) { /* pas de décor : la carte reste sans fond */ }
  appliquerTheme(); afficher();
}
sombre.addEventListener("change", () => { appliquerTheme(); afficher(); });

// Une seule NOUVELLE cellule par image : le calcul des bâtiments d'une cellule (plusieurs dizaines de milliers de polygones) bloque l'image ;
// en les ajoutant une par une, le déplacement de la carte reste fluide au lieu de se figer quand plusieurs cellules arrivent ensemble.
const cellulesMontrees = new Set();
let ajoutEnAttente = false;
function cellulesAMontrer() {
  const pretes = clesPretes(), sortie = [];
  let nouvelle = false, attente = false;
  for (const k of cellulesMontrees) if (!pretes.includes(k)) cellulesMontrees.delete(k);   // sorties du champ : à re-étaler quand elles reviennent
  for (const k of pretes) {
    if (!cellulesMontrees.has(k)) { if (nouvelle) { attente = true; continue; } cellulesMontrees.add(k); nouvelle = true; }
    sortie.push(k);
  }
  if (attente && !ajoutEnAttente) { ajoutEnAttente = true; requestAnimationFrame(() => { ajoutEnAttente = false; afficher(); }); }
  return sortie;
}

function calquesDecor() {
  const c = palette(), theme = styleCarte + (sombre.matches ? 1 : 0), L = [], r = RENDU[styleCarte];
  const cellules = indexDecor ? cellulesAMontrer().map(k => [k, cellulesDecor.get(k)]) : (decor ? [["massy", decor]] : []);
  for (const [k, cell] of cellules) {
    L.push(new deck.PolygonLayer({ id: "surfaces-" + k, data: cell.surfaces, getPolygon: d => d.p, getFillColor: d => c[d.t], updateTriggers: { getFillColor: [theme] }, stroked: false, filled: true }));
    L.push(new deck.PathLayer({ id: "lignes-" + k, data: cell.lignes, getPath: d => d.c, getColor: d => c[d.t], getWidth: d => d.w, widthUnits: "meters", widthMinPixels: 0.6,
      capRounded: true, jointRounded: true, updateTriggers: { getColor: [theme] } }));
    L.push(new deck.PathLayer({ id: "rails-" + k, data: cell.rails, getPath: d => d.c, getColor: d => c.rail, getWidth: d => d.w, widthUnits: "meters", widthMinPixels: 1.1,
      capRounded: true, jointRounded: true, updateTriggers: { getColor: [theme] } }));
    L.push(new deck.PolygonLayer({ id: "batiments-" + k, data: cell.batiments, getPolygon: d => d.p, extruded: true, getElevation: d => d.h,
      getFillColor: d => d.h_src === "defaut" ? c.bat_defaut : c.bat, updateTriggers: { getFillColor: [theme], getLineColor: [theme] },
      material: r ? r.material : { ambient: 0.55, diffuse: 0.6, shininess: 8, specularColor: [60, 64, 80] }, stroked: false,
      ...(r && r.fil ? { wireframe: true, getLineColor: r.fil, lineWidthMinPixels: 1 } : {}),
      ...(r && r.translucide ? { parameters: { depthWriteEnabled: false } } : {}) }));
  }
  return L;
}

// --- noms de gares
// Noms de gares lisibles sur tout fond : texte clair à contour sombre en thème sombre, texte sombre à contour clair en thème clair.
const COULEUR_NOM = { clair: { texte: [20, 30, 40, 255], contour: [255, 255, 255, 245] }, sombre: { texte: [255, 255, 255, 255], contour: [14, 20, 30, 235] } };
const couleurNomGare = () => COULEUR_NOM[sombre.matches ? "sombre" : "clair"];
// --- fin noms de gares

// --- voitures
// Rame articulée : chaque caisse est posée sur la voie du train (tracé envoyé par le serveur, dans le sens de la marche) comme sur ses deux bogies :
// le centre de la caisse est le milieu des deux pivots (à ±9,25 m du centre), son cap est la direction de la corde entre eux. En courbe, la rame se plie ;
// en ligne droite le résultat est identique à la rame rigide. Rang 0 = motrice de tête, 1 et 2 = remorques, 3 = motrice de queue.
const CENTRES_VOITURES_M = [39.0125, 13, -13, -39.0125];   // abscisse du centre de chaque caisse depuis le centre de la rame (tête devant)
const DEMI_PIVOTS_M = 9.25;                                 // les pivots de bogie sont à 18,5 m l'un de l'autre
const echellesLocales = lat => [111320 * Math.cos(lat * Math.PI / 180), 110540];   // mètres par degré de longitude et de latitude

// Rame droite de repli (pas de voie connue : terminus, serveur sans tracé) : voie fictive de 400 m dans la direction du cap.
function voieDroite(lon, lat, cap) {
  const [kx, ky] = echellesLocales(lat), a = (cap || 0) * Math.PI / 180;
  return [[lon - Math.sin(a) * 200 / kx, lat - Math.cos(a) * 200 / ky], [lon + Math.sin(a) * 200 / kx, lat + Math.cos(a) * 200 / ky]];
}

// Projette le centre de la rame sur la voie : renvoie { kx, ky, s0, point(s) } (s0 = abscisse du centre le long de la voie, point(s) = [x, y] en mètres autour du centre, la voie
// étant prolongée en ligne droite aux deux bouts), ou null si la voie est inutilisable.
function projeterSurVoie(voie, lon, lat) {
  if (!voie || voie.length < 2) return null;
  const [kx, ky] = echellesLocales(lat);
  const P = voie.map(([x, y]) => [(x - lon) * kx, (y - lat) * ky]);
  const cum = [0];
  for (let i = 1; i < P.length; i++) cum.push(cum[i - 1] + Math.hypot(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]));
  if (!(cum[cum.length - 1] > 0)) return null;
  const dernier = P.length - 2;
  let s0 = 0, meilleur = Infinity;
  for (let i = 0; i <= dernier; i++) {   // projection de l'origine sur chaque segment ; les deux segments d'extrémité sont prolongés
    const ex = P[i + 1][0] - P[i][0], ey = P[i + 1][1] - P[i][1], L2 = ex * ex + ey * ey;
    if (!(L2 > 0)) continue;
    let t = (-P[i][0] * ex - P[i][1] * ey) / L2;
    if (i > 0) t = Math.max(0, t);
    if (i < dernier) t = Math.min(1, t);
    const d = Math.hypot(P[i][0] + t * ex, P[i][1] + t * ey);
    if (d < meilleur) { meilleur = d; s0 = cum[i] + t * Math.sqrt(L2); }
  }
  const total = cum[cum.length - 1];
  const point = s => {
    let i = 0;
    if (s >= total) i = dernier; else if (s > 0) while (i < dernier && cum[i + 1] < s) i++;
    const L = cum[i + 1] - cum[i] || 1, u = (s - cum[i]) / L;   // u < 0 ou > 1 : prolongement droit
    return [P[i][0] + (P[i + 1][0] - P[i][0]) * u, P[i][1] + (P[i + 1][1] - P[i][1]) * u];
  };
  return { kx, ky, s0, point };
}

// voie : [[lon, lat], ...] dans le sens de la marche ; (lon, lat) : centre de la rame (projeté sur la voie, prolongée en ligne droite aux deux bouts).
// Renvoie les 4 caisses : [{ lon, lat, cap }] (cap en degrés, 0 = nord, sens horaire), ou null si la voie est inutilisable.
function voituresSurVoie(voie, lon, lat) {
  const pv = projeterSurVoie(voie, lon, lat);
  if (!pv) return null;
  const { kx, ky, s0, point } = pv;
  return CENTRES_VOITURES_M.map(c => {
    const [ax, ay] = point(s0 + c - DEMI_PIVOTS_M), [bx, by] = point(s0 + c + DEMI_PIVOTS_M);
    return { lon: lon + (ax + bx) / 2 / kx, lat: lat + (ay + by) / 2 / ky, cap: (Math.atan2(bx - ax, by - ay) * 180 / Math.PI + 360) % 360 };
  });
}

// Axe de la rame le long de la voie (queue -> tête), de -demi à +demi autour du centre : [[x, y], ...] en mètres autour du centre ; null si la voie est inutilisable.
function axeSurVoie(voie, lon, lat, demi, pas = 8) {
  const pv = projeterSurVoie(voie, lon, lat);
  if (!pv) return null;
  const n = Math.max(1, Math.ceil(2 * demi / pas));
  return Array.from({ length: n + 1 }, (_, k) => pv.point(pv.s0 - demi + 2 * demi * k / n));
}

// Les caisses de tous les trains : un objet par caisse, avec les champs du train (clic, info-bulle, couleur) et son rang.
function voitures(p) {
  return p.flatMap(t => {
    const caisses = voituresSurVoie(t.voie, t.lon, t.lat) || voituresSurVoie(voieDroite(t.lon, t.lat, t.cap), t.lon, t.lat);
    return caisses.map((c, rang) => ({ ...t, ...c, rang }));
  });
}
// Les maillages sont orientés tête en +x ; la caisse 2 est la remorque tournée de 180°, la caisse 3 la motrice tournée de 180°.
const orientationVoiture = d => [0, 90 - d.cap + (d.rang >= 2 ? 180 : 0), 0];
// --- fin voitures

// --- contour
// Contour d'état en « gélule » posé au sol autour de la rame (maquette validée le 18/11 : web/tableau-rendu.html) : un tracé par état, épaisseur en PIXELS constante, identique
// de près et de loin ; plusieurs états = tracés emboîtés, le plus important au plus près de la rame. De près, un socle de la couleur de l'état (à quai, en route, terminus, données
// anciennes) remplit la gélule : il remplace le disque de l'icône de loin. La gélule suit la voie (rame pliée en courbe).
const PX_TRAIT = 2.4, PX_ECART = 3.5, PX_PULSE = 1.6, DEMI_LARGEUR_CAISSE_M = 1.45;
const ROUGE_TERMINUS_VIF = [255, 60, 75];
// États qui dessinent un contour, du plus important au moins important ; « retenu » = jaune, attente en voie après une révision PRIM de plus de 120 s.
const contoursDeTrain = (t, selectionne) => [
  t.terminus && { couleur: ROUGE_TERMINUS_VIF, pulse: true },
  t.retenu && { couleur: JAUNE_RETENU, pulse: true },
  selectionne && { couleur: COULEUR_SELECTION, pulse: false },
  t.donnees_anciennes && { couleur: ROSE_ANCIENNES, pulse: false },
].filter(Boolean);

// Contour fermé en mètres [x, y] autour de l'axe (queue -> tête) à la distance r : deux côtés parallèles et deux demi-cercles aux bouts.
function gelule(axe, r, pasDeg = 15) {
  const n = axe.length, cap = i => { const a = axe[Math.max(0, i - 1)], b = axe[Math.min(n - 1, i + 1)], dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1; return [dx / l, dy / l]; };
  const gauche = [], droite = [];
  axe.forEach((q, i) => { const [tx, ty] = cap(i); gauche.push([q[0] - ty * r, q[1] + tx * r]); droite.push([q[0] + ty * r, q[1] - tx * r]); });
  const demiCercle = (c, [tx, ty]) => {   // de la gauche à la droite en passant par l'avant de la direction (tx, ty)
    const h = Math.atan2(ty, tx) * 180 / Math.PI, pts = [];
    for (let a = h + 90; a >= h - 90 - 1e-9; a -= pasDeg) pts.push([c[0] + r * Math.cos(a * Math.PI / 180), c[1] + r * Math.sin(a * Math.PI / 180)]);
    return pts;
  };
  const avant = demiCercle(axe[n - 1], cap(n - 1)), arriere = demiCercle(axe[0], cap(0).map(v => -v));   // arrière : le même arc, direction inversée (de la droite vers la gauche de la rame)
  const ferme = [...gauche, ...avant, ...droite.reverse(), ...arriere];
  ferme.push(ferme[0]);
  return ferme;
}

// Axe, demi-largeur et demi-longueur (mètres) de l'emprise d'un train : le long de la voie de près (modèle détaillé), droit selon le cap de loin (icône rigide).
function empriseTrain(t, ech) {
  const demiLarg = ech.detail ? DEMI_LARGEUR_CAISSE_M * ech.largeur : LARGEUR_ICONE_M / 2 * ech.largeur;
  const demi = Math.max(0, LONGUEUR_RAME_M / 2 * ech.longueur - demiLarg);
  const axeVoie = ech.detail ? (axeSurVoie(t.voie, t.lon, t.lat, demi) || axeSurVoie(voieDroite(t.lon, t.lat, t.cap), t.lon, t.lat, demi)) : null;
  const a = (t.cap || 0) * Math.PI / 180;
  return { demiLarg, axe: axeVoie || [[-Math.sin(a) * demi, -Math.cos(a) * demi], [Math.sin(a) * demi, Math.cos(a) * demi]] };
}

// Couches des socles et des contours. `p` : trains affichés (avec alpha) ; `ech` : échelle ; `mpp` : mètres par pixel ; `pulse` : 0 à 1 ; `selectionnee` : course sélectionnée.
function calquesRames(p, ech, mpp, pulse, selectionnee) {
  const lonLat = (t, q) => { const [kx, ky] = echellesLocales(t.lat); return [t.lon + q[0] / kx, t.lat + q[1] / ky]; };
  const hauteur = ech.detail ? 0.4 : 0.4 * mpp;   // au-dessus du sol (de loin : ~0,4 px) ; le socle est juste dessous
  const rames = p.map(t => ({ t, ...empriseTrain(t, ech), contours: contoursDeTrain(t, t.course === selectionnee) }));
  const traits = rames.flatMap(({ t, axe, demiLarg, contours }) => contours.map((c, rang) => ({
    chemin: gelule(axe, demiLarg + (PX_ECART + rang * (PX_TRAIT + PX_ECART)) * mpp).map(q => [...lonLat(t, q), hauteur]),
    couleur: [...c.couleur, Math.round(255 * t.alpha)], largeur: PX_TRAIT + (c.pulse ? PX_PULSE * pulse : 0),
  })));
  const L = [];
  if (styleCarte === "verre") L.push(new deck.ScatterplotLayer({
    id: "rames-halo", data: rames, getPosition: d => [d.t.lon, d.t.lat], radiusUnits: "meters", getRadius: 70, radiusMinPixels: 14,
    getFillColor: d => [...couleurTrain(d.t), Math.round(46 * d.t.alpha)], parameters: { depthWriteEnabled: false },
  }));
  if (ech.detail) {
    L.push(new deck.PolygonLayer({
      id: "rames-socle", data: rames, getPolygon: d => gelule(d.axe, d.demiLarg + PX_ECART * mpp).map(q => [...lonLat(d.t, q), 0.15]),
      getFillColor: d => [...couleurTrain(d.t), Math.round(110 * d.t.alpha)], stroked: false, parameters: { depthWriteEnabled: false },
    }));
  }
  L.push(new deck.PathLayer({
    id: "rames-contour", data: traits, getPath: d => d.chemin, getColor: d => d.couleur, getWidth: d => d.largeur, widthUnits: "pixels", jointRounded: true, capRounded: true,
  }));
  return L;
}
// --- fin contour

// Couches d'un niveau de modèle (détaillé ou icône) : une couche par pièce (couleur). L'icône a un disque dessous, coloré selon l'état du train (le détaillé a un socle, voir calquesRames) ;
// les vitres du modèle détaillé sont semi-transparentes et dessinées en dernier sans écrire la profondeur.
function calquesTrains(p, ech, couleurAlpha) {
  const matiere = { ambient: 0.55, diffuse: 0.65, shininess: 24, specularColor: [90, 90, 90] };
  if (ech.detail) {   // de près : une couche par pièce et par type de caisse, chaque caisse posée sur la voie
    const caisses = voitures(p);
    return [["motrice", d => d.rang === 0 || d.rang === 3], ["remorque", d => d.rang === 1 || d.rang === 2]].flatMap(([type, est]) => {
      const donnees = caisses.filter(est);
      return MODELES[type].pieces.map(piece => {
        const vitre = piece.nom === "vitre";
        return new deck.SimpleMeshLayer({
          id: `trains-detail-${type}-${piece.nom}`, data: donnees, mesh: piece.maillage, getPosition: d => [d.lon, d.lat, 0],
          getColor: d => [...piece.couleur, Math.round((vitre ? 90 : 255) * d.alpha)],
          getOrientation: orientationVoiture, getScale: [1, ech.largeur, ech.largeur], updateTriggers: { getScale: [ech.largeur] }, sizeScale: 1, pickable: !vitre, material: matiere,
          parameters: vitre ? { depthWriteEnabled: false } : undefined,
        });
      });
    });
  }
  const echelle = () => [ech.longueur, ech.largeur, ech.largeur];   // de loin : icône rigide, agrandie
  return MODELES.icone.pieces.map(piece => {
    const vitre = piece.nom === "vitre", disque = piece.nom === "disque";
    return new deck.SimpleMeshLayer({
      id: `trains-icone-${piece.nom}`, data: p, mesh: piece.maillage, getPosition: d => [d.lon, d.lat, 0],
      getColor: d => disque ? couleurAlpha(d) : [...(PALETTE_ICONE[piece.nom] || piece.couleur), Math.round((vitre ? 90 : 255) * d.alpha)],
      getOrientation: orientation, getScale: echelle, updateTriggers: { getScale: [ech.longueur, ech.largeur], getColor: [] }, sizeScale: 1,
      pickable: !vitre, material: matiere,
      parameters: vitre ? { depthWriteEnabled: false } : undefined,
    });
  });
}

function calques(maintenant = performance.now()) {
  const L = [];
  if (!geo) return L;
  L.push(...calquesDecor());
  const rendu = RENDU[styleCarte], tr = rendu && rendu.trace;
  if (tr && tr.halo) L.push(new deck.PathLayer({
    id: "trace-halo", data: geo.trace, getPath: d => d, getColor: tr.halo, widthUnits: "meters", getWidth: tr.largeur * 5,
    widthMinPixels: 8, capRounded: true, jointRounded: true, parameters: { depthWriteEnabled: false },
  }));
  L.push(new deck.PathLayer({
    id: "trace", data: geo.trace, getPath: d => d, getColor: tr ? tr.couleur : [120, 132, 142], widthUnits: "meters", getWidth: tr ? tr.largeur : 5,
    widthMinPixels: tr ? 2 : 1.5, capRounded: true, jointRounded: true, updateTriggers: { getColor: [styleCarte] },
  }));
  L.push(new deck.ScatterplotLayer({
    id: "gares", data: geo.gares, getPosition: d => [d.lon, d.lat], getRadius: 35, radiusMinPixels: 3, radiusMaxPixels: 9,
    getFillColor: [255, 255, 255], getLineColor: [29, 42, 51], stroked: true, lineWidthMinPixels: 1.2, pickable: true,
  }));
  const nom = styleCarte !== "actuel" ? COULEUR_NOM.sombre : couleurNomGare();
  L.push(new deck.TextLayer({
    id: "noms-gares", data: geo.gares, getPosition: d => [d.lon, d.lat], getText: d => d.nom, getSize: 13, sizeUnits: "pixels",
    getPixelOffset: [9, -9], getTextAnchor: "start", getColor: nom.texte, characterSet: "auto", fontFamily: "system-ui, sans-serif", fontWeight: 600,
    visible: zoom >= 11.5, background: false, outlineWidth: 4, outlineColor: nom.contour, fontSettings: { sdf: true }, pickable: true,
    updateTriggers: { getColor: [sombre.matches, styleCarte], outlineColor: [sombre.matches, styleCarte] },
    parameters: { depthCompare: "always" },   // jamais cachés par les bâtiments extrudés (Cité Universitaire)
  }));
  const p0 = placesAffiches(maintenant);
  const vp = deckgl && deckgl.getViewports ? deckgl.getViewports()[0] : null;
  const rg = vp ? regrouper(p0.map(t => { const [x, y] = vp.project([t.lon, t.lat]); return { course: t.course, x, y }; }), selection) : { alpha: {}, bulles: [] };
  groupes = new Map(rg.bulles.map(b => [b.chef, b.membres]));
  const p = p0.map(t => ({ ...t, alpha: rg.alpha[t.course] == null ? 1 : rg.alpha[t.course] })).filter(t => t.alpha > 0.02);
  const couleurAlpha = t => [...couleurTrain(t), Math.round(255 * t.alpha)];
  if (mode !== "train") {   // incertitude : chemin approximatif le long de la voie (serveur, J2 bis, points ~8 s), jamais un disque : l'erreur est le long des rails ; non ancré sur la position animée (saute à chaque relevé)
    const avecChemin = (d, cle) => d.alpha > 0.3 && d.incertitude && d.incertitude[cle] && d.incertitude[cle].length > 1;
    for (const [id, cle, largeur, couleur] of [["incertitude-p90", "p90", 26, [214, 158, 46, 70]], ["incertitude-mediane", "mediane", 14, [183, 121, 31, 130]]]) {
      L.push(new deck.PathLayer({
        id, data: p.filter(d => avecChemin(d, cle)), getPath: d => d.incertitude[cle].map(([lat, lon]) => [lon, lat, Z_BANDE[cle]]),
        getColor: couleur, widthUnits: "meters", getWidth: largeur, widthMinPixels: id === "incertitude-p90" ? 5 : 3, capRounded: true, jointRounded: true,
        parameters: { depthWriteEnabled: false },   // translucide : ne cache ni le pied des trains ni l'autre bande (z-fighting avec le sol)
      }));
    }
  }
  if (mode === "zone") {
    L.push(new deck.ScatterplotLayer({
      id: "pastilles", data: p, getPosition: d => [d.lon, d.lat], getRadius: 1, radiusMinPixels: 6,
      radiusMaxPixels: 7, getFillColor: couleurAlpha, getLineColor: [255, 255, 255], stroked: true, lineWidthMinPixels: 1.5,
      pickable: true,
    }));
  }
  if (mode !== "zone") {   // socle et contours d'état autour des rames (remplacent le halo rouge du terminus et l'anneau de sélection)
    const mpp = 78271.517 * Math.cos(48.8 * Math.PI / 180) / Math.pow(2, zoom);
    L.push(...calquesRames(p, echelleTrain(zoom), mpp, 0.5 + 0.5 * Math.sin(maintenant / 280), selection));
  }
  if (mode !== "zone") L.push(...calquesTrains(p, echelleTrain(zoom), couleurAlpha));
  if (vp && rg.bulles.length) {   // bulle « n » à côté du train chef ; son opacité est l'inverse de celle des trains qui s'effacent
    const bulles = rg.bulles.map(b => { const [lon, lat] = vp.unproject([b.x + 13, b.y - 13]); return { ...b, lon, lat }; });
    L.push(new deck.ScatterplotLayer({
      id: "bulles", data: bulles, getPosition: d => [d.lon, d.lat], getRadius: 1, radiusMinPixels: 10, radiusMaxPixels: 10, stroked: true,
      getFillColor: d => [29, 42, 51, Math.round(235 * d.alpha)], getLineColor: d => [255, 255, 255, Math.round(255 * d.alpha)], lineWidthMinPixels: 1.5, pickable: true,
    }));
    L.push(new deck.TextLayer({
      id: "bulles-nombre", data: bulles, getPosition: d => [d.lon, d.lat], getText: d => String(d.n), getSize: 12, sizeUnits: "pixels",
      getColor: d => [255, 255, 255, Math.round(255 * d.alpha)], characterSet: "auto", fontFamily: "system-ui, sans-serif", fontWeight: 700,
      getTextAnchor: "middle", getAlignmentBaseline: "center",
    }));
  }
  return L;
}

// --- Deck
let deckgl = null;
function afficher(maintenant = performance.now()) { if (deckgl) deckgl.setProps({ layers: calques(maintenant) }); }

// --- Vue : état piloté par nous (viewState contrôlé), pour que la caméra puisse suivre un train ou voler vers une gare sans combat avec deck.gl.
// Déplacer = glisser (comme Google Maps / Google Earth). Tourner / incliner = clic droit ou Ctrl + glisser, deux doigts, glisser la boussole, ou boutons.
const LIMITES_VUE = { minZoom: 8, maxZoom: 21, maxPitch: MAX_PITCH };
const nettoyerVue = v => ({ longitude: v.longitude, latitude: v.latitude, zoom: v.zoom, pitch: v.pitch || 0, bearing: v.bearing || 0, ...LIMITES_VUE });
let vue = nettoyerVue({ ...MASSY, zoom: ZOOM_DEPART, pitch: 55, bearing: 0 });

function appliquerVue(v) {
  vue = nettoyerVue(v);
  vueCarte = { longitude: vue.longitude, latitude: vue.latitude, zoom: vue.zoom, pitch: vue.pitch, bearing: vue.bearing };
  zoom = vue.zoom; pasZoom = Math.round(zoom * 10);
  if (deckgl) deckgl.setProps({ viewState: vue });
  majBoussole(); majDecor();
}

// --- vol de caméra
// Vol (durée fixe, ease in-out) de la vue actuelle vers une cible. La cible peut être un train : on vise sa position AFFICHÉE à chaque image,
// donc la caméra arrive exactement dessus et continue de le suivre (`suivi`). Pour un long trajet, la caméra dézoome en cours de route.
const easeInOut = f => (f < 0.5 ? 2 * f * f : 1 - Math.pow(-2 * f + 2, 2) / 2);
function etapeVol(de, vers, f, dip) {
  const e = easeInOut(f);
  return {
    longitude: de.longitude + (vers.longitude - de.longitude) * e, latitude: de.latitude + (vers.latitude - de.latitude) * e,
    zoom: de.zoom + (vers.zoom - de.zoom) * e - dip * Math.sin(Math.PI * f),
    pitch: de.pitch + (vers.pitch - de.pitch) * e, bearing: de.bearing + ecartAngle(de.bearing, vers.bearing) * e,
  };
}
function voler(cible, duree = 900) {   // cible : objet de vue, ou fonction qui le renvoie (suivi d'un train)
  const de = { ...vueCarte };
  const but = typeof cible === "function" ? cible() : cible;
  const km = Math.hypot((but.longitude - de.longitude) * 74, (but.latitude - de.latitude) * 111);
  vol = { t0: performance.now(), duree, de, cible, dip: km > 2 ? Math.min(2.5, Math.log2(km) * 0.6) : 0 };
  animer();
}
// --- fin vol de caméra

function etapeCamera(maintenant) {   // appelée à chaque image : fait avancer le vol, puis recale la caméra sur le train suivi
  let v = null;
  if (vol) {
    const f = Math.min(1, (maintenant - vol.t0) / vol.duree), but = typeof vol.cible === "function" ? vol.cible() : vol.cible;
    v = etapeVol(vol.de, but, f, vol.dip);
    if (f >= 1) vol = null;
  } else if (suivi) {
    const c = cibleSuivi();
    if (c) v = { ...vueCarte, longitude: c.longitude, latitude: c.latitude };
  }
  if (v) appliquerVue(v);
}

// Boutons : tourner (± 30°), incliner (± 10°), boussole (clic = nord en haut). Vol court, centre et zoom inchangés.
function changerVue(dBearing, dPitch) { voler({ ...vueCarte, ...pasVue(vueCarte, dBearing, dPitch) }, 300); }
function majBoussole() {
  const hud = $("reperes-vue");
  if (hud) hud.textContent = `CAP ${String(Math.round(((vueCarte.bearing || 0) % 360 + 360) % 360)).padStart(3, "0")}° · INCL ${String(Math.round(vueCarte.pitch || 0)).padStart(2, "0")}° · ZOOM ${(vueCarte.zoom || 0).toFixed(1)} · POSITIONS ESTIMÉES, PAS GPS`;
  const b = $("boussole");
  if (b) b.style.setProperty("--cap", `${-(vueCarte.bearing || 0)}deg`);
  const c = $("boussole-carte");
  if (c) c.style.setProperty("--cap", `${-(vueCarte.bearing || 0)}deg`);
}
const vueDeck = () => new deck.MapView({ id: "carte", controller: { dragMode: "pan", dragRotate: true, touchRotate: true, keyboard: true, inertia: 300, scrollZoom: { speed: 0.012, smooth: true } } });

// --- suivi d'un train / aller à une gare
let suivi = null, vol = null;
function trainAffiche(course) { return placesAffiches(performance.now()).find(t => t.course === course); }
function cibleSuivi() {
  const t = suivi && trainAffiche(suivi);
  return t ? { longitude: t.lon, latitude: t.lat } : null;
}
function arreterSuivi() { suivi = null; vol = null; majFiche(); }
function suivreTrain(course) {
  const t = trainAffiche(course);
  if (!t) return;
  selection = course; suivi = course; groupeOuvert = null;
  voler(() => { const c = cibleSuivi() || { longitude: t.lon, latitude: t.lat }; return { ...c, zoom: Math.max(vueCarte.zoom, ZOOM_SUIVI), pitch: Math.max(vueCarte.pitch, 50), bearing: vueCarte.bearing }; }, 1100);
  detail_train(); majFiche(); afficherProchains(); afficher();
}
const ZOOM_SUIVI = 18, ZOOM_GARE = 17.2;
function allerGare(g) {
  suivi = null; selection = null;
  gareChoisie = g.ref; dernierePrevision = null; sequenceGare++;
  const choix = $("choix-gare"); if (choix) choix.value = g.ref;
  voler({ longitude: g.lon, latitude: g.lat, zoom: ZOOM_GARE, pitch: 60, bearing: vueCarte.bearing }, 1100);
  detail_train(); majFiche(); afficherProchains(); actualiserGare(); afficher();
}
// --- fin suivi

function creerDeck() {
  deckgl = new deck.Deck({
    parent: document.getElementById("carte"),
    views: vueDeck(),
    viewState: vue,
    layers: [],
    getTooltip: ({ object, layer }) => {
      if (!object) return null;
      if (layer.id === "gares" || layer.id === "noms-gares") return object.nom + " : cliquer pour zoomer";
      if (object.membres) return object.n + " trains proches : cliquer pour la liste";
      if (object.course) return `${object.etat}${suffixeEtiquettes(object)} : ${object.gare || "?"}${object.gare_suivante ? " → " + object.gare_suivante : ""} (cliquer pour le suivre)`;
      return null;
    },
    onViewStateChange: ({ viewState, interactionState }) => {
      const avantPas = pasZoom, avantNoms = zoom >= 11.5, avantCles = clesPretes().join();
      const i = interactionState || {};
      // glisser pour déplacer = l'utilisateur prend la main, on ne suit plus (le zoom à la molette déplace aussi un peu : il ne compte pas)
      if (i.isDragging && i.isPanning && !i.isZooming) { if (suivi) arreterSuivi(); vol = null; }
      else if (i.isDragging || i.isZooming || i.isRotating) vol = null;
      appliquerVue(viewState);
      if (Math.round(zoom * 10) !== avantPas || (zoom >= 11.5) !== avantNoms || clesPretes().join() !== avantCles) afficher();
    },
    onClick: ({ object, layer }) => {
      if (object && (layer.id === "gares" || layer.id === "noms-gares")) { allerGare(object); return; }   // gare : zoom dessus
      if (object && object.membres) { groupeOuvert = object.chef; selection = object.chef; suivi = null; }   // bulle : liste des trains du groupe
      else if (object && object.course) { suivreTrain(object.course); return; }                             // train : zoom, suivi et fiche
      else if (!object) { selection = null; groupeOuvert = null; if (suivi) arreterSuivi(); }
      detail_train(); majFiche(); afficherProchains(); afficher();
    },
  });
}

function boiteLigne() {
  let [x0, y0, x1, y1] = [1e9, 1e9, -1e9, -1e9];
  geo.trace.forEach(l => l.forEach(([x, y]) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }));
  return { longitude: (x0 + x1) / 2, latitude: (y0 + y1) / 2, zoom: 9.6, pitch: 0, bearing: 0 };
}

// --- Panneau
const $ = id => document.getElementById(id);
// Toute donnée venue de PRIM (références de course, gares, motifs) est échappée avant d'entrer dans du HTML.
const esc = x => String(x == null ? "" : x).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function minutes(s) { return s == null ? "inconnu" : (s >= 0 ? "+" : "−") + Math.round(Math.abs(s) / 60) + " min"; }

function ligneGroupe(m) {   // un train d'un groupe de la bulle ; toute donnée PRIM passe par esc()
  return '<div class="special"><b>' + esc(m.etat) + esc(suffixeEtiquettes(m)) + "</b> : " + esc(m.gare || "?")
    + (m.gare_suivante ? " → " + esc(m.gare_suivante) : "") + "<br>Retard : " + minutes(m.retard_s) + "</div>";
}

function corpsTrain(t) {   // toute donnée PRIM passe par esc()
  return `<b>${esc(t.etat)}${esc(suffixeEtiquettes(t))}</b>${etiquettes(t).map(e => `<br><span class="note"><b>${e.libelle}</b> : ${e.note}</span>`).join("")}<br>Gare : ${esc(t.gare || "—")}${t.gare_suivante ? "<br>Prochaine gare : " + esc(t.gare_suivante) : ""}`
    + `<br>Retard : ${minutes(t.retard_s)}<br>Mise à jour du train il y a ${t.age_s == null ? "?" : Math.round(t.age_s / 60)} min`
    + (t.horaires_incoherents ? `<br><b class="retard">⚠ ${esc(t.motif_vitesse)}</b>` : "")
    + (t.reconstruit ? "<br><i>heures reconstruites (gare retirée par PRIM)</i>" : "") + `<br><span class="note">${esc(t.course)}</span>`;
}

function detail_train() {
  const membres = groupeOuvert && groupes.get(groupeOuvert);
  $("fiche-groupe").hidden = !membres;   // un seul train : sa fiche est sur la carte (#fiche-train) ; ici seulement la liste d'une bulle de trains proches
  if (membres) $("detail").innerHTML = "<b>" + membres.length + " trains proches</b>" + membres.map(c => trains.find(x => x.course === c)).filter(Boolean).map(ligneGroupe).join("");
}

// Fiche flottante sur la carte : infos du train choisi, mises à jour à chaque relevé ; bouton pour suivre ou arrêter de suivre.
function majFiche() {
  const f = $("fiche-train"), t = trains.find(x => x.course === selection);
  if (!f) return;
  f.hidden = !t || !!groupeOuvert;
  if (f.hidden) return;
  $("fiche-corps").innerHTML = corpsTrain(t);
  $("fiche-suivi").textContent = suivi === selection ? "Caméra sur le train : arrêter" : "Suivre ce train";
}

function panneau() {
  if (statut) {
    $("bandeau-demo").hidden = !statut.demo;
    const e = $("bandeau-erreur");
    e.hidden = !(statut.erreur || statut.bloque);
    e.textContent = statut.bloque ? "Clé PRIM refusée ou quota atteint : plus aucun appel. " + (statut.erreur || "")
      : (statut.erreur ? "Dernier appel PRIM en échec : " + statut.erreur + ". Les positions affichées sont celles du dernier relevé valide." : "");
    $("statut").innerHTML = statut.releve_age_s == null ? "Aucun relevé reçu."
      : (statut.demo ? `Relevé de démonstration il y a ${statut.releve_age_s} s. Aucun appel PRIM.`
        : `Dernier relevé PRIM il y a ${statut.releve_age_s} s.<br>Appels PRIM aujourd'hui : ${statut.plafond.utilises} / ${statut.plafond.maximum}.`);
  }
  const n = e => trains.filter(t => t.etat === e).length;
  const p = placés();
  $("compteurs").innerHTML = `${trains.length} trains reçus : ${p.length} placés (${n("à quai")} à quai, ${n("en route")} en route, `
    + `${p.filter(t => t.donnees_anciennes).length} à données anciennes, ${p.filter(t => t.horaire_theorique).length} à l'horaire théorique), ${n("non placé")} non placés, ${n("terminé")} terminés, ${n("position inconnue")} position inconnue.`;
  const incoherents = trains.filter(t => t.horaires_incoherents).length;
  if (incoherents) $("compteurs").append(document.createElement("br"), `⚠ ${incoherents} train(s) aux horaires PRIM incohérents (vitesse limitée à 119 km/h).`);
  const speciaux = trains.filter(t => t.lat == null && t.etat !== "terminé");
  $("liste-speciaux").innerHTML = speciaux.slice(0, 12).map(t =>
    `<div class="special"><b>${esc(t.etat)}</b> <span class="motif">${esc(t.motif)}</span><br><span class="note">${esc(t.course)}</span></div>`).join("")
    + (speciaux.length > 12 ? `<div class="note">… et ${speciaux.length - 12} autres</div>` : "");
}

// --- Prochains trains à la gare choisie (stop-monitoring, via /gare : cache 60 s côté serveur)
const heureParis = iso => new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
function ecrire(el, ...noeuds) { el.replaceChildren(...noeuds); }
function noeud(tag, texte, classe) { const n = document.createElement(tag); if (texte != null) n.textContent = texte; if (classe) n.className = classe; return n; }

function afficherProchains() {
  const d = dernierePrevision, corps = $("prochains").tBodies[0], etat = $("etat-gare");
  if (!d) { ecrire(corps); etat.textContent = "Chargement…"; return; }
  const messages = [];
  if (d.demo) messages.push("démonstration : trains inventés");
  if (d.bloque) messages.push("clé PRIM refusée ou quota atteint : plus aucun appel");
  else if (d.statut === "plafond") messages.push("plafond d'appels du jour atteint");
  else if (d.statut === "erreur") messages.push("dernier appel en échec (" + d.erreur + ")");
  if (d.age_s != null) messages.push("données de la gare il y a " + d.age_s + " s");
  etat.textContent = messages.join(" ; ") || "Aucune donnée.";
  const maintenant = Date.now();
  const lignes = d.passages.map(p => {
    const tr = noeud("tr", null, "ligne" + (p.course === selection ? " choisi" : ""));
    const minutesAvant = Math.round((new Date(p.heure_estimee) - maintenant) / 60000);
    tr.append(noeud("td", p.a_quai ? "à quai" : heureParis(p.heure_estimee), "heure"));
    const infos = noeud("td");
    infos.append(noeud("div", p.destination || "?"));
    const detail = noeud("div", null, "note");
    detail.append((p.mission ? p.mission + " · " : "") + (p.a_quai ? "" : (minutesAvant <= 0 ? "maintenant" : "dans " + minutesAvant + " min")));
    infos.append(detail);
    tr.append(infos);
    const droite = noeud("td");
    if (p.annule) droite.append(noeud("span", "annulé", "badge annule"));
    else if (p.retard_s == null) droite.append(noeud("span", "retard ?", "note"));
    else droite.append(noeud("span", Math.abs(p.retard_s) < 60 ? "à l'heure" : "+" + Math.round(p.retard_s / 60) + " min", Math.abs(p.retard_s) < 60 ? "ponctuel" : "retard"));
    tr.append(droite);
    tr.addEventListener("click", () => { if (trainAffiche(p.course)) { suivreTrain(p.course); return; } selection = p.course; detail_train(); majFiche(); afficherProchains(); afficher(); });
    return tr;
  });
  if (!lignes.length) { const tr = noeud("tr"); tr.append(noeud("td", d.statut === "plafond" ? "—" : "Aucun passage à venir dans la réponse.")); lignes.push(tr); }
  ecrire(corps, ...lignes);
}

let sequenceGare = 0, gareEnCours = false;
async function actualiserGare() {
  if (!interrogationActive(document.hidden)) return;   // onglet caché : pas d'appel
  if (gareEnCours) return;                        // pas d'empilement si le serveur est lent
  gareEnCours = true;
  const demandee = gareChoisie, numero = ++sequenceGare;
  let reponse;
  try {
    const r = await fetch("/gare?ref=" + encodeURIComponent(demandee), { cache: "no-store" });
    reponse = r.ok ? await r.json() : { passages: [], statut: "erreur", erreur: "serveur HTTP " + r.status, age_s: null };
  } catch (e) {
    reponse = { passages: [], statut: "erreur", erreur: "serveur local injoignable", age_s: null };
  }
  gareEnCours = false;
  if (demandee !== gareChoisie || numero !== sequenceGare) { actualiserGare(); return; }   // la gare a changé pendant l'appel : réponse jetée
  dernierePrevision = reponse;
  afficherProchains();
}

let trainsEnCours = false;
async function actualiser() {
  if (!interrogationActive(document.hidden)) return;   // onglet caché : on n'interroge pas, donc on ne déclenche aucun appel PRIM
  if (trainsEnCours) return;                      // pas d'empilement : une seule requête /trains à la fois
  trainsEnCours = true;
  debutRequeteMs = performance.now();
  setTimeout(majChargement, 900);                 // la pastille « mise à jour » n'apparaît que si la requête traîne
  const abandon = new AbortController(), minuteur = setTimeout(() => abandon.abort(), DELAI_REQUETE_MS);
  try {
    const r = await fetch("/trains", { cache: "no-store", signal: abandon.signal });
    const d = await r.json();
    if (!Array.isArray(d.trains)) throw new Error("réponse invalide");
    statut = d; trains = d.trains; derniereReponseMs = performance.now(); majAffiches(derniereReponseMs);
    if (d.trains.length || (!d.erreur && !d.bloque)) premiereReponse = true;
    if (selection && !trains.some(t => t.course === selection)) { selection = null; suivi = null; }   // le train choisi a disparu
  } catch (e) {
    const lent = e && e.name === "AbortError";
    statut = { ...(statut || {}), erreur: lent ? "le serveur local ne répond pas" : "serveur local injoignable", bloque: false, plafond: (statut && statut.plafond) || { utilises: "?", maximum: "?" } };
  }
  clearTimeout(minuteur);
  trainsEnCours = false; reessai = false; reveil = false; debutRequeteMs = null;
  majChargement(); panneau(); detail_train(); majFiche(); afficherProchains(); afficher(); animer();
}


// --- état du chargement
let premiereReponse = false, debutAttente = performance.now(), reessai = false, reveil = false;
let derniereReponseMs = null, debutRequeteMs = null, chargementForce = null, actionReessayer = null;
function afficherChargement(e) {
  const b = $("chargement");
  b.hidden = !e;
  if (!e) return;
  b.dataset.type = e.type;
  $("chargement-message").textContent = e.message;
  $("chargement-reessayer").hidden = !e.reessayer;
}
let accueilFerme = false, accueilVu = false;
try { accueilVu = sessionStorage.getItem("accueil-vu") === "1"; } catch (e) { /* stockage de session indisponible : on présente à chaque chargement */ }
function majAccueil() {
  const e = etatAccueil({ vu: accueilVu, recu: premiereReponse, ferme: accueilFerme });
  const a = $("accueil");
  a.hidden = !e.afficher;
  a.classList.toggle("compact", e.compact);
  $("accueil-entrer").disabled = !e.bouton;
  $("accueil-entrer").textContent = e.bouton ? "Voir les trains" : "Chargement…";
}
function majChargement() {
  if (chargementForce) { afficherChargement(chargementForce); return; }
  const erreur = statut && (statut.erreur || null);
  afficherChargement(etatChargement({ recu: premiereReponse, attenteMs: performance.now() - debutAttente, erreur, bloque: !!(statut && statut.bloque), reessai }));
  const p = $("etat-donnees"), maintenant = performance.now();
  const e = premiereReponse ? etatDonnees({
    enCours: trainsEnCours && debutRequeteMs != null && maintenant - debutRequeteMs > 800,   // pas de clignotement pour une requête rapide
    ageS: derniereReponseMs == null ? 0 : (maintenant - derniereReponseMs) / 1000,
    erreur, bloque: !!(statut && statut.bloque), reveil }) : null;
  p.hidden = !e;
  if (e) { p.dataset.type = e.type; p.textContent = e.message; }
  majAccueil();
}

function reglerDirection(d) {
  filtreDirection = d;
  const info = DIRECTIONS[d];
  $("direction-texte").textContent = "Trains affichés : " + info.titre + " (" + info.detail + ")";
  $("direction-changer").textContent = "Voir les trains vers le " + DIRECTIONS[info.autre].titre.replace("vers le ", "").toLowerCase();
  document.querySelectorAll("input[name=direction]").forEach(i => { i.checked = i.value === d; });
  if (suivi && !garderTrain(trains.find(t => t.course === suivi) || {}, d)) arreterSuivi();
  afficher();
}

function initialiserRecherche() {
  const champ = $("recherche"), liste = $("suggestions");
  let courantes = [], actif = -1;
  const fermer = () => { liste.hidden = true; courantes = []; actif = -1; champ.setAttribute("aria-expanded", "false"); };
  const choisir = g => { champ.value = g.nom; fermer(); champ.blur(); allerGare(g); };
  const surligner = () => [...liste.children].forEach((li, k) => { li.classList.toggle("actif", k === actif); li.setAttribute("aria-selected", k === actif); });
  function maj_() {
    courantes = suggestionsGares(geo.gares, champ.value);
    liste.replaceChildren(...courantes.map((g, k) => {
      const li = noeud("li", g.nom); li.setAttribute("role", "option");
      li.addEventListener("mousedown", e => { e.preventDefault(); choisir(g); });
      li.addEventListener("mousemove", () => { actif = k; surligner(); });
      return li;
    }));
    if (champ.value && !courantes.length) { const li = noeud("li", "Aucune gare de ce nom", "vide"); liste.replaceChildren(li); }
    liste.hidden = !champ.value; actif = courantes.length ? 0 : -1; surligner(); champ.setAttribute("aria-expanded", String(!liste.hidden));
  }
  champ.addEventListener("input", maj_);
  champ.addEventListener("focus", () => { champ.select(); if (champ.value) maj_(); });
  champ.addEventListener("blur", fermer);
  champ.addEventListener("keydown", e => {
    if (e.key === "ArrowDown" && courantes.length) { actif = (actif + 1) % courantes.length; surligner(); e.preventDefault(); }
    else if (e.key === "ArrowUp" && courantes.length) { actif = (actif - 1 + courantes.length) % courantes.length; surligner(); e.preventDefault(); }
    else if (e.key === "Enter" && courantes.length) { choisir(courantes[Math.max(0, actif)]); e.preventDefault(); }
    else if (e.key === "Escape") { champ.value = ""; fermer(); champ.blur(); }
  });
  addEventListener("keydown", e => { if (e.key === "/" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); champ.focus(); } });
}

async function demarrer() {
  $("chargement-reessayer").addEventListener("click", () => {
    if (actionReessayer) { actionReessayer(); return; }
    reessai = true; debutAttente = performance.now(); majChargement(); actualiser();
  });
  const minuterieChargement = setInterval(() => { if (premiereReponse) clearInterval(minuterieChargement); majChargement(); }, 1000);
  document.querySelectorAll("input[name=mode]").forEach(i => i.addEventListener("change", () => {
    mode = i.value; $("avertissement-train").hidden = mode !== "train"; afficher();
  }));
  document.querySelectorAll("input[name=direction]").forEach(i => i.addEventListener("change", () => reglerDirection(i.value)));
  $("direction-changer").addEventListener("click", () => reglerDirection(DIRECTIONS[filtreDirection].autre));
  reglerDirection(filtreDirection);
  try {
    const rg = await fetch("/geo");
    if (!rg.ok) throw new Error("HTTP " + rg.status);
    geo = await rg.json();
  } catch (e) {
    const b = $("bandeau-erreur"); b.hidden = false;
    b.textContent = "Impossible de charger le tracé (" + e.message + "). Le serveur local tourne-t-il ? Recharge la page.";
    chargementForce = { type: "echec", reessayer: true, message: "Le tracé de la ligne n'a pas pu être chargé (" + e.message + "). Le serveur local tourne-t-il ?" };
    actionReessayer = () => location.reload();
    majChargement();
    return;
  }
  const choix = $("choix-gare");
  [...geo.gares].sort((a, b) => a.nom.localeCompare(b.nom, "fr")).forEach(g => {
    const o = noeud("option", g.nom); o.value = g.ref; choix.append(o);
  });
  choix.value = gareChoisie;
  choix.addEventListener("change", () => { gareChoisie = choix.value; dernierePrevision = null; sequenceGare++; afficherProchains(); actualiserGare(); });
  creerDeck();
  // ouverture : la caméra s'éloigne vers toute la ligne (sauf vue imposée par l'adresse ; sans animation si l'utilisateur la réduit)
  if (!new URLSearchParams(location.search).get("vue")) {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) appliquerVue(boiteLigne()); else voler(boiteLigne(), 2600);
  }
  chargerDecor();
  initialiserRecherche();
  $("vue-ligne").addEventListener("click", () => voler(boiteLigne(), 1400));
  $("tourner-gauche").addEventListener("click", () => changerVue(-30, 0));
  $("tourner-droite").addEventListener("click", () => changerVue(30, 0));
  $("incliner-plus").addEventListener("click", () => changerVue(0, 10));
  $("incliner-moins").addEventListener("click", () => changerVue(0, -10));
  $("boussole").addEventListener("click", () => changerVue(-(vueCarte.bearing || 0), 0));
  // boussole : un clic remet le nord en haut ; la glisser tourne (horizontal) et incline (vertical), pour les trackpads sans clic droit
  let boussole = null, boussoleGlissee = false;
  $("boussole-carte").addEventListener("pointerdown", e => {
    boussole = { x: e.clientX, y: e.clientY, fait: false }; boussoleGlissee = false;
    $("boussole-carte").setPointerCapture(e.pointerId);
  });
  $("boussole-carte").addEventListener("pointermove", e => {
    if (!boussole) return;
    const dx = e.clientX - boussole.x, dy = e.clientY - boussole.y;
    if (!boussole.fait && Math.hypot(dx, dy) < 4) return;   // sous 4 px : c'est un clic
    boussole.fait = boussoleGlissee = true; boussole.x = e.clientX; boussole.y = e.clientY;
    const d = deltaVue(dx, dy);
    vol = null;
    appliquerVue({ ...vueCarte, ...pasVue(vueCarte, d.dBearing, d.dPitch) });
    afficher();
  });
  const finBoussole = () => { boussole = null; };
  $("boussole-carte").addEventListener("pointerup", finBoussole);
  $("boussole-carte").addEventListener("pointercancel", finBoussole);
  $("boussole-carte").addEventListener("click", () => { if (boussoleGlissee) { boussoleGlissee = false; return; } changerVue(-(vueCarte.bearing || 0), 0); });
  appliquerTheme();
  $("zoom-plus").addEventListener("click", () => voler({ ...vueCarte, zoom: vueCarte.zoom + 1 }, 300));
  $("zoom-moins").addEventListener("click", () => voler({ ...vueCarte, zoom: vueCarte.zoom - 1 }, 300));
  $("incliner-carte").addEventListener("click", () => voler({ ...vueCarte, pitch: vueCarte.pitch > 20 ? 0 : 60 }, 500));   // 2D / 3D
  $("fiche-suivi").addEventListener("click", () => { if (suivi === selection) arreterSuivi(); else suivreTrain(selection); });
  const fermerFiches = () => { selection = null; groupeOuvert = null; arreterSuivi(); detail_train(); majFiche(); afficherProchains(); afficher(); };
  $("fiche-fermer").addEventListener("click", fermerFiches);
  $("groupe-fermer").addEventListener("click", fermerFiches);
  $("accueil-entrer").addEventListener("click", () => {
    accueilFerme = true;
    try { sessionStorage.setItem("accueil-vu", "1"); } catch (e) { /* tant pis : présentée à nouveau au prochain chargement */ }
    majAccueil();
  });
  $("accueil-revoir").addEventListener("click", () => { accueilFerme = false; accueilVu = false; $("d-apropos").open = false; majAccueil(); });
  // vue de départ réglable par l'adresse : ?vue=longitude,latitude,zoom,inclinaison,cap (utile pour partager ou tester une vue)
  const v = new URLSearchParams(location.search).get("vue");
  if (v) {
    const [longitude, latitude, z, pitch, bearing] = v.split(",").map(Number);
    if ([longitude, latitude, z].every(Number.isFinite)) appliquerVue({ longitude, latitude, zoom: z, pitch: pitch || 0, bearing: bearing || 0 });
  }
  // retour sur l'onglet : reprise immédiate (le serveur rappelle PRIM si son relevé a plus de 120 s)
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    if (!premiereReponse) debutAttente = performance.now();
    reveil = true; actualiser(); actualiserGare();
  });
  await actualiser();
  setInterval(actualiser, INTERVALLE_MS);
  actualiserGare();
  setInterval(actualiserGare, 15000);   // le serveur ne rappelle PRIM qu'au plus toutes les 60 s par gare
}

demarrer();
