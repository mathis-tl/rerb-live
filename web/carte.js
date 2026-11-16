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

let geo = null, trains = [], statut = null, mode = "mixte", rayon = 700, zoom = 9, selection = null, gareChoisie = "58774", dernierePrevision = null;

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

// --- Animation PAR IDENTIFIANT de course (pas par rang : deck.gl apparie par rang, et la liste change d'ordre à chaque
// relevé, ce qui faisait glisser un train depuis la position d'un autre). Chaque train glisse de sa position affichée vers
// la nouvelle ; une correction de plus de 250 m (nouveau relevé PRIM) glisse plus lentement pour rester visible comme
// correction, au lieu d'un saut. Train nouveau : posé directement. Train disparu : retiré.
const affiches = new Map();     // course -> { de:[lon,lat,cap], vers:[lon,lat,cap], t0, duree }
const SEUIL_CORRECTION_M = 250, DUREE_CORRECTION_MAX_MS = 12000;
const metres = (a, b) => Math.hypot((b[0] - a[0]) * 74000, (b[1] - a[1]) * 111000);
function ecartAngle(a, b) { return ((b - a + 540) % 360) - 180; }

function positionAffichee(e, maintenant) {
  const f = e.duree <= 0 ? 1 : Math.min(1, Math.max(0, (maintenant - e.t0) / e.duree));
  const k = f * f * (3 - 2 * f);     // adouci
  return [e.de[0] + (e.vers[0] - e.de[0]) * k, e.de[1] + (e.vers[1] - e.de[1]) * k, e.de[2] + ecartAngle(e.de[2], e.vers[2]) * k, f];
}

function majAffiches(maintenant) {
  const presents = new Set();
  placés().forEach(t => {
    presents.add(t.course);
    const vers = [t.lon, t.lat, t.cap == null ? 0 : t.cap];
    const e = affiches.get(t.course);
    if (!e) { affiches.set(t.course, { de: vers, vers, t0: maintenant, duree: 0 }); return; }
    const [lon, lat, cap] = positionAffichee(e, maintenant);
    const saut = metres([lon, lat], vers);
    e.de = [lon, lat, cap]; e.vers = vers; e.t0 = maintenant;
    e.duree = saut > SEUIL_CORRECTION_M ? Math.min(DUREE_CORRECTION_MAX_MS, saut / 50 * 1000) : INTERVALLE_MS;
  });
  for (const c of [...affiches.keys()]) if (!presents.has(c)) affiches.delete(c);
}

// trains placés, avec la position affichée (interpolée) à la place de la position reçue
function placesAffiches(maintenant) {
  return placés().map(t => {
    const e = affiches.get(t.course);
    if (!e) return t;
    const [lon, lat, cap] = positionAffichee(e, maintenant);
    return { ...t, lon, lat, cap };
  });
}

let animation = null;
function animer() {
  if (animation) return;
  const pas = () => {
    animation = null;
    const maintenant = performance.now();
    afficher(maintenant);
    if ([...affiches.values()].some(e => maintenant - e.t0 < e.duree)) animation = setTimeout(() => requestAnimationFrame(pas), 60);
  };
  animation = setTimeout(() => requestAnimationFrame(pas), 0);
}

function calques(maintenant = performance.now()) {
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
    if (mode !== "train" && rayon > 0) {
    L.push(new deck.ScatterplotLayer({
      id: "zone", data: p, getPosition: d => [d.lon, d.lat], getRadius: rayon, radiusUnits: "meters", filled: true, stroked: true,
      getFillColor: [214, 158, 46, 55], getLineColor: [183, 121, 31, 200], lineWidthMinPixels: 1.5,
      updateTriggers: { getRadius: rayon },
    }));
  }
  if (mode === "zone" || zoom < ZOOM_PASTILLE) {
    L.push(new deck.ScatterplotLayer({
      id: "pastilles", data: p, getPosition: d => [d.lon, d.lat], getRadius: 1, radiusMinPixels: mode === "zone" ? 6 : 5,
      radiusMaxPixels: 7, getFillColor: couleurTrain, getLineColor: [255, 255, 255], stroked: true, lineWidthMinPixels: 1.5,
      pickable: true,
    }));
  }
  if (selection) {
    L.push(new deck.ScatterplotLayer({
      id: "selection", data: p.filter(d => d.course === selection), getPosition: d => [d.lon, d.lat], getRadius: 1,
      radiusMinPixels: 16, radiusMaxPixels: 16, filled: false, stroked: true, getLineColor: [245, 158, 11], lineWidthMinPixels: 3,
    }));
  }
  if (mode !== "zone") {
    L.push(new deck.SimpleMeshLayer({
      id: "trains", data: p, mesh: MAILLAGE_CORPS, getPosition: d => [d.lon, d.lat, 0], getColor: couleurTrain,
      getOrientation: orientation, sizeScale: 1, pickable: true,
      material: { ambient: 0.55, diffuse: 0.6, shininess: 24, specularColor: [200, 200, 200] },
    }));
    L.push(new deck.SimpleMeshLayer({
      id: "cabines", data: p, mesh: MAILLAGE_CABINE, getPosition: d => [d.lon, d.lat, 0], getColor: [38, 42, 48],
      getOrientation: orientation, sizeScale: 1,
    }));
  }
  return L;
}

// --- Deck
let deckgl = null;
function afficher(maintenant = performance.now()) { if (deckgl) deckgl.setProps({ layers: calques(maintenant) }); }

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
      detail_train(); afficherProchains(); afficher();
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

function detail_train() {
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
    tr.addEventListener("click", () => { selection = p.course; detail_train(); afficherProchains(); afficher(); });
    return tr;
  });
  if (!lignes.length) { const tr = noeud("tr"); tr.append(noeud("td", d.statut === "plafond" ? "—" : "Aucun passage à venir dans la réponse.")); lignes.push(tr); }
  ecrire(corps, ...lignes);
}

async function actualiserGare() {
  try {
    const r = await fetch("/gare?ref=" + encodeURIComponent(gareChoisie), { cache: "no-store" });
    dernierePrevision = r.ok ? await r.json() : { passages: [], statut: "erreur", erreur: "serveur HTTP " + r.status, age_s: null };
  } catch (e) {
    dernierePrevision = { passages: [], statut: "erreur", erreur: "serveur local injoignable", age_s: null };
  }
  afficherProchains();
}

async function actualiser() {
  try {
    const r = await fetch("/trains", { cache: "no-store" });
    const d = await r.json();
    statut = d; trains = d.trains; majAffiches(performance.now());
  } catch (e) {
    statut = { ...(statut || {}), erreur: "serveur local injoignable", bloque: false, plafond: (statut && statut.plafond) || { utilises: "?", maximum: "?" } };
  }
  panneau(); detail_train(); afficher(); animer();
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
  const choix = $("choix-gare");
  [...geo.gares].sort((a, b) => a.nom.localeCompare(b.nom, "fr")).forEach(g => {
    const o = noeud("option", g.nom); o.value = g.ref; choix.append(o);
  });
  choix.value = gareChoisie;
  choix.addEventListener("change", () => { gareChoisie = choix.value; dernierePrevision = null; afficherProchains(); actualiserGare(); });
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
  actualiserGare();
  setInterval(actualiserGare, 15000);   // le serveur ne rappelle PRIM qu'au plus toutes les 60 s par gare
}

demarrer();
