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
const ZOOM_PASTILLE = 14.5;            // en dessous, la rame (105 m) est trop petite : on ajoute une pastille de repère
const COULEURS = { "à quai": [47, 133, 90], "en route": [43, 108, 176] };
const GRIS = [160, 167, 173];
const MASSY = { longitude: 2.2588, latitude: 48.7255 };

let geo = null, trains = [], statut = null, mode = "mixte", rayon = 700, zoom = 9, selection = null;

// --- Rame provisoire : 6 caisses en boîtes (105 m), cabine de tête plus sombre. La rame détaillée viendra en dernier.
const L_CAISSE = 16.5, ECART = 1.2, LARGEUR = 2.8, HAUTEUR = 4.3;

function boite(x0, x1, y0, y1, z0, z1, positions, normales, indices) {
  const faces = [   // [normale, 4 sommets]
    [[0, 0, 1], [[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]],
    [[0, 0, -1], [[x0, y1, z0], [x1, y1, z0], [x1, y0, z0], [x0, y0, z0]]],
    [[1, 0, 0], [[x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1]]],
    [[-1, 0, 0], [[x0, y1, z0], [x0, y0, z0], [x0, y0, z1], [x0, y1, z1]]],
    [[0, 1, 0], [[x1, y1, z0], [x0, y1, z0], [x0, y1, z1], [x1, y1, z1]]],
    [[0, -1, 0], [[x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1]]],
  ];
  for (const [n, sommets] of faces) {
    const base = positions.length / 3;
    for (const s of sommets) { positions.push(...s); normales.push(...n); }
    indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
}

function maillage(boites) {
  const positions = [], normales = [], indices = [];
  boites.forEach(b => boite(...b, positions, normales, indices));
  return {
    positions: { value: new Float32Array(positions), size: 3 },
    normals: { value: new Float32Array(normales), size: 3 },
    indices: { value: new Uint16Array(indices), size: 1 },
  };
}

const longueur = 6 * L_CAISSE + 5 * ECART;
const caisses = [];
for (let i = 0; i < 6; i++) {
  const x0 = -longueur / 2 + i * (L_CAISSE + ECART);
  caisses.push([x0, x0 + L_CAISSE, -LARGEUR / 2, LARGEUR / 2, 0.6, HAUTEUR]);
}
const MAILLAGE_CORPS = maillage(caisses);
const MAILLAGE_CABINE = maillage([[longueur / 2 - 5, longueur / 2 + 0.3, -LARGEUR / 2 - 0.1, LARGEUR / 2 + 0.1, 0.6, HAUTEUR - 0.6]]);

// --- Couleur et orientation
const couleurTrain = t => (t.perime ? GRIS : (COULEURS[t.etat] || GRIS));
// cap serveur : degrés, 0 = nord, sens horaire. Orientation deck.gl : [tangage, lacet, roulis], lacet antihoraire depuis l'est.
const orientation = t => [0, 90 - (t.cap == null ? 0 : t.cap), 0];

const placés = () => trains.filter(t => t.lat != null && t.lon != null);

function calques() {
  const L = [];
  if (!geo) return L;
  L.push(new deck.PathLayer({
    id: "trace", data: geo.trace, getPath: d => d, getColor: [120, 132, 142], widthUnits: "meters", getWidth: 5,
    widthMinPixels: 1.5, capRounded: true, jointRounded: true,
  }));
  L.push(new deck.ScatterplotLayer({
    id: "gares", data: geo.gares, getPosition: d => [d.lon, d.lat], getRadius: 35, radiusMinPixels: 3, radiusMaxPixels: 9,
    getFillColor: [255, 255, 255], getLineColor: [29, 42, 51], stroked: true, lineWidthMinPixels: 1.2, pickable: true,
  }));
  L.push(new deck.TextLayer({
    id: "noms-gares", data: geo.gares, getPosition: d => [d.lon, d.lat], getText: d => d.nom, getSize: 12, sizeUnits: "pixels",
    getPixelOffset: [8, -8], getTextAnchor: "start", getColor: [29, 42, 51], characterSet: "auto", fontFamily: "system-ui, sans-serif",
    visible: zoom >= 11.5, background: false, outlineWidth: 3, outlineColor: [255, 253, 248, 230], fontSettings: { sdf: true },
  }));
  const p = placés();
  const transition = { getPosition: { duration: INTERVALLE_MS, type: "interpolation" }, getOrientation: 600 };
  if (mode !== "train" && rayon > 0) {
    L.push(new deck.ScatterplotLayer({
      id: "zone", data: p, getPosition: d => [d.lon, d.lat], getRadius: rayon, radiusUnits: "meters", filled: true, stroked: true,
      getFillColor: [214, 158, 46, 55], getLineColor: [183, 121, 31, 200], lineWidthMinPixels: 1.5, transitions: transition,
      updateTriggers: { getRadius: rayon },
    }));
  }
  if (mode === "zone" || zoom < ZOOM_PASTILLE) {
    L.push(new deck.ScatterplotLayer({
      id: "pastilles", data: p, getPosition: d => [d.lon, d.lat], getRadius: 1, radiusMinPixels: mode === "zone" ? 6 : 5,
      radiusMaxPixels: 7, getFillColor: couleurTrain, getLineColor: [255, 255, 255], stroked: true, lineWidthMinPixels: 1.5,
      pickable: true, transitions: transition,
    }));
  }
  if (mode !== "zone") {
    L.push(new deck.SimpleMeshLayer({
      id: "trains", data: p, mesh: MAILLAGE_CORPS, getPosition: d => [d.lon, d.lat, 0], getColor: couleurTrain,
      getOrientation: orientation, sizeScale: 1, pickable: true, transitions: transition,
      material: { ambient: 0.55, diffuse: 0.6, shininess: 24, specularColor: [200, 200, 200] },
    }));
    L.push(new deck.SimpleMeshLayer({
      id: "cabines", data: p, mesh: MAILLAGE_CABINE, getPosition: d => [d.lon, d.lat, 0], getColor: [38, 42, 48],
      getOrientation: orientation, sizeScale: 1, transitions: transition,
    }));
  }
  return L;
}

// --- Deck
let deckgl = null;
function afficher() { if (deckgl) deckgl.setProps({ layers: calques() }); }

function creerDeck() {
  deckgl = new deck.Deck({
    parent: document.getElementById("carte"),
    views: new deck.MapView({ id: "carte", controller: true }),
    initialViewState: { ...MASSY, zoom: 10.5, pitch: 55, bearing: 0, minZoom: 8, maxZoom: 21, maxPitch: 80 },
    controller: true,
    layers: [],
    getTooltip: ({ object, layer }) => {
      if (!object) return null;
      if (layer.id === "gares") return object.nom;
      if (object.course) return `${object.etat}${object.perime ? " (périmé)" : ""} : ${object.gare || "?"}${object.gare_suivante ? " → " + object.gare_suivante : ""}`;
      return null;
    },
    onViewStateChange: ({ viewState }) => {
      const avant = zoom >= ZOOM_PASTILLE, avantNoms = zoom >= 11.5;
      zoom = viewState.zoom;
      if ((zoom >= ZOOM_PASTILLE) !== avant || (zoom >= 11.5) !== avantNoms) afficher();
    },
    onClick: ({ object }) => {
      selection = object && object.course ? object.course : selection;
      detail();
    },
  });
}

function aller(vue) {
  deckgl.setProps({ initialViewState: { ...vue, transitionDuration: 900, transitionInterpolator: new deck.FlyToInterpolator() } });
  zoom = vue.zoom;
  afficher();
}

function boiteLigne() {
  let [x0, y0, x1, y1] = [1e9, 1e9, -1e9, -1e9];
  geo.trace.forEach(l => l.forEach(([x, y]) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }));
  return { longitude: (x0 + x1) / 2, latitude: (y0 + y1) / 2, zoom: 9.6, pitch: 0, bearing: 0 };
}

// --- Panneau
const $ = id => document.getElementById(id);
function minutes(s) { return s == null ? "inconnu" : (s >= 0 ? "+" : "−") + Math.round(Math.abs(s) / 60) + " min"; }

function detail() {
  const t = trains.find(x => x.course === selection);
  $("detail").innerHTML = t
    ? `<b>${t.etat}${t.perime ? " (périmé)" : ""}</b><br>Gare : ${t.gare || "—"}${t.gare_suivante ? "<br>Prochaine gare : " + t.gare_suivante : ""}`
      + `<br>Retard : ${minutes(t.retard_s)}<br>Mise à jour du train il y a ${t.age_s == null ? "?" : Math.round(t.age_s / 60)} min`
      + (t.reconstruit ? "<br><i>heures reconstruites (gare retirée par PRIM)</i>" : "") + `<br><span class="note">${t.course}</span>`
    : "Clique sur un train.";
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
    + `${p.filter(t => t.perime).length} périmés), ${n("non placé")} non placés, ${n("terminé")} terminés, ${n("position inconnue")} position inconnue.`;
  const speciaux = trains.filter(t => t.lat == null && t.etat !== "terminé");
  $("liste-speciaux").innerHTML = speciaux.slice(0, 12).map(t =>
    `<div class="special"><b>${t.etat}</b> <span class="motif">${t.motif || ""}</span><br><span class="note">${t.course}</span></div>`).join("")
    + (speciaux.length > 12 ? `<div class="note">… et ${speciaux.length - 12} autres</div>` : "");
}

async function actualiser() {
  try {
    const r = await fetch("/trains", { cache: "no-store" });
    const d = await r.json();
    statut = d; trains = d.trains;
  } catch (e) {
    statut = { ...(statut || {}), erreur: "serveur local injoignable", bloque: false, plafond: (statut && statut.plafond) || { utilises: "?", maximum: "?" } };
  }
  panneau(); detail(); afficher();
}

async function demarrer() {
  document.querySelectorAll("input[name=mode]").forEach(i => i.addEventListener("change", () => {
    mode = i.value; $("avertissement-train").hidden = mode !== "train"; afficher();
  }));
  const r = $("rayon");
  const fixer = v => { rayon = +v; r.value = v; $("valeur-rayon").textContent = rayon + " m"; afficher(); };
  r.addEventListener("input", () => fixer(r.value));
  document.querySelectorAll("[data-rayon]").forEach(b => b.addEventListener("click", () => fixer(b.dataset.rayon)));
  geo = await (await fetch("/geo")).json();
  creerDeck();
  $("vue-ligne").addEventListener("click", () => aller(boiteLigne()));
  $("vue-massy").addEventListener("click", () => aller({ ...MASSY, zoom: 13.2, pitch: 55, bearing: 0 }));
  $("vue-quai").addEventListener("click", () => aller({ longitude: 2.25877, latitude: 48.72552, zoom: 17.5, pitch: 62, bearing: 30 }));
  // vue de départ réglable par l'adresse : ?vue=longitude,latitude,zoom,inclinaison,cap (utile pour partager ou tester une vue)
  const v = new URLSearchParams(location.search).get("vue");
  if (v) {
    const [longitude, latitude, z, pitch, bearing] = v.split(",").map(Number);
    if ([longitude, latitude, z].every(Number.isFinite)) aller({ longitude, latitude, zoom: z, pitch: pitch || 0, bearing: bearing || 0 });
  }
  await actualiser();
  setInterval(actualiser, INTERVALLE_MS);
}

demarrer();
