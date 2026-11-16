"""J4 (tâche 4) : MAQUETTE visuelle des rendus de la carte, à ouvrir dans un navigateur. HORS LIGNE, aucun appel API.

Usage : python3 -I maquette_rendus.py [SORTIE.html]       (défaut : web/maquette-rendus.html du dépôt)

Données : vrai tracé et vraies gares du dépôt (fichiers statiques) ; les TRAINS SONT INVENTÉS (démonstration du rendu,
pas des positions réelles). Aucune bibliothèque externe, aucun fond de carte en ligne : la page n'envoie rien dehors.
Rendus montrés : zone seule, zone + train à l'échelle, train seul ; rayon de zone réglable ; états des trains.
"""
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / "rerb"))

from geometry import Geometry, load_morceaux  # noqa: E402
from position import Reseau  # noqa: E402
from stops import load_stops  # noqa: E402
from troncon import chemin_troncon  # noqa: E402

CSV = RACINE / "rerb" / "donnees" / "arrets_rerb_C01743.csv"
TRACE = RACINE / "data" / "static" / "rerb" / "rerb_trace_C01743.json"

# (étiquette, état, gare de départ, gare d'arrivée, avancement 0..1 si à quai ou figé) : numéros de gares (CSV)
DEMO = [
    ("Train A (à quai)", "a_quai", "58774", "47940", 0.0),     # Massy - Palaiseau -> Massy - Verrières
    ("Train B (en route)", "en_route", "58774", "47940", None),  # animé sur le même tronçon
    ("Train C (périmé, grisé)", "perime", "58774", "47940", 0.55),
]


def arrondi(points):
    sortie = []
    for lat, lon in points:
        p = (round(lat, 5), round(lon, 5))
        if not sortie or p != sortie[-1]:
            sortie.append(p)
    return sortie


def main():
    sortie = Path(sys.argv[1]) if len(sys.argv) > 1 else RACINE / "web" / "maquette-rendus.html"
    gares = load_stops(CSV)
    geometrie = Geometry(load_morceaux(TRACE), {n: (s.lat, s.lon) for n, s in gares.items()})
    reseau = Reseau({p: geometrie.distance(*p) for p in geometrie.neighbor_pairs()})
    donnees = {
        "trace": [arrondi(m) for m in load_morceaux(TRACE)],
        "gares": [{"nom": s.name, "lat": round(s.lat, 5), "lon": round(s.lon, 5)} for s in gares.values()],
        "trains": [{"nom": nom, "etat": etat, "chemin": arrondi(chemin_troncon(geometrie, reseau, a, b)), "avance": av}
                   for nom, etat, a, b, av in DEMO],
    }
    html = MODELE.replace("__DONNEES__", json.dumps(donnees, ensure_ascii=False, separators=(",", ":")))
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(html, encoding="utf-8")
    print(f"maquette écrite : {sortie} ({len(html) // 1024} Ko, {sum(len(m) for m in donnees['trace'])} points de tracé)")


MODELE = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>RER B - maquette des rendus (données inventées)</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 :root{--fond:#f4f1ea;--encre:#1d2a33;--rail:#8a96a0;--rer:#2b6cb0;--perime:#a0a7ad;--zone:rgba(214,158,46,.28);--zone-b:#b7791f}
 *{box-sizing:border-box} body{margin:0;font:15px/1.4 system-ui,sans-serif;color:var(--encre);background:var(--fond);display:flex;height:100vh}
 #panneau{width:340px;padding:14px 16px;overflow:auto;background:#fffdf8;border-right:1px solid #d9d2c3}
 h1{font-size:17px;margin:0 0 4px} h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;margin:16px 0 6px;color:#5b6770}
 .note{font-size:13px;color:#5b6770} .bandeau{background:#fff3cd;border:1px solid #e6c860;padding:6px 8px;border-radius:6px;font-size:13px;margin:8px 0}
 label.choix{display:block;padding:6px 8px;border:1px solid #d9d2c3;border-radius:6px;margin:4px 0;cursor:pointer;background:#fff}
 label.choix input{margin-right:6px} label.choix.on{border-color:var(--rer);background:#eaf2fb}
 button{font:inherit;padding:5px 9px;margin:2px 4px 2px 0;border:1px solid #b9b19f;border-radius:6px;background:#fff;cursor:pointer} button:hover{background:#eef}
 input[type=range]{width:100%} #valR{font-weight:600}
 #carte{flex:1;position:relative} canvas{width:100%;height:100%;display:block;cursor:grab} canvas:active{cursor:grabbing}
 #legende{position:absolute;left:12px;bottom:12px;background:rgba(255,253,248,.93);border:1px solid #d9d2c3;border-radius:8px;padding:8px 10px;font-size:13px}
 #legende b{display:block;margin-bottom:3px} .pastille{display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:6px;vertical-align:-1px}
 #echelle{position:absolute;right:14px;bottom:14px;background:rgba(255,253,248,.93);border:1px solid #d9d2c3;border-radius:6px;padding:4px 8px;font-size:12px}
 #info{position:absolute;right:14px;top:12px;background:rgba(255,253,248,.93);border:1px solid #d9d2c3;border-radius:8px;padding:6px 10px;font-size:13px;max-width:300px}
</style></head><body>
<div id="panneau">
 <h1>RER B - maquette des rendus</h1>
 <div class="bandeau"><b>Trains inventés.</b> Vrai tracé et vraies gares, positions de démonstration. Aucune donnée PRIM, aucun appel réseau, aucun fond de carte en ligne.</div>

 <h2>1. Ce qu'on affiche pour un train</h2>
 <label class="choix" id="c-zone"><input type="radio" name="mode" value="zone"><b>Zone seule</b><br><span class="note">Cercle autour de la position estimée. Honnête tant que l'erreur reste grande.</span></label>
 <label class="choix" id="c-mixte"><input type="radio" name="mode" value="mixte" checked><b>Zone + train à l'échelle</b><br><span class="note">Étape intermédiaire : le train (vraie longueur) dans sa zone d'incertitude.</span></label>
 <label class="choix" id="c-train"><input type="radio" name="mode" value="train"><b>Train seul à l'échelle</b><br><span class="note">Objectif final. N'est honnête que si l'erreur passe sous ~100 m.</span></label>

 <h2>2. Rayon de la zone : <span id="valR">700 m</span></h2>
 <input type="range" id="rayon" min="0" max="2000" step="10" value="700">
 <div class="note">Repères : <button data-r="100">≈ 100 m (objectif)</button><button data-r="650">≈ 650 m (médiane J2 bis*)</button><button data-r="1720">≈ 1 720 m (90e centile*)</button></div>
 <div class="note">* ordre de grandeur : 44 s et 117 s d'erreur de départ × 14,7 m/s, un seul tronçon, <b>pas une erreur mesurée en mètres</b>.</div>

 <h2>3. Aller voir</h2>
 <button id="v-ligne">Toute la ligne</button><button id="v-massy">Secteur Massy</button><button id="v-gare">Quai de Massy (échelle rame)</button>
 <div class="note">Molette : zoom. Glisser : déplacer. La rame mesure 105 m : <b>invisible en vue large (pastille de repère), nette au quai</b>.</div>

 <h2>4. Vitesse de démonstration</h2>
 <input type="range" id="vitesse" min="1" max="20" value="1"><div class="note">× <span id="valV">1</span> (1 = vitesse réelle 14,7 m/s)</div>

 <h2>Ce que montre cette maquette</h2>
 <div class="note">• L'état <b>à quai</b> (A), <b>en route</b> animé (B : il avance entre deux relevés), <b>périmé</b> grisé (C).<br>
 • Un train « non placé » ne serait pas dessiné : il figurerait dans une liste à part, jamais masqué.<br>
 • Légende toujours visible : « position estimée, pas GPS ».<br>
 • Le rendu 3D (modèle de rame) vient après : cette vue de dessus sert de base.</div>
</div>
<div id="carte"><canvas id="cv"></canvas>
 <div id="info"></div>
 <div id="legende"><b>Position estimée, pas GPS</b>
  <span class="pastille" style="background:#2b6cb0"></span>en route &nbsp; <span class="pastille" style="background:#2f855a"></span>à quai &nbsp; <span class="pastille" style="background:#a0a7ad"></span>périmé<br>
  <span class="pastille" style="background:rgba(214,158,46,.5);border:1px solid #b7791f"></span>zone = erreur supposée</div>
 <div id="echelle"></div></div>
<script>
const D = __DONNEES__;
const LAT0 = 48.80, LON0 = 2.35, KX = 111320*Math.cos(LAT0*Math.PI/180), KY = 110540;
const toM = (lat,lon)=>[(lon-LON0)*KX,(lat-LAT0)*KY];
const trace = D.trace.map(l=>l.map(p=>toM(p[0],p[1])));
const gares = D.gares.map(g=>({nom:g.nom,p:toM(g.lat,g.lon)}));
const L_RAME = 105, W_RAME = 3.0, NB_CAISSES = 6, V_REEL = 14.7;
const trains = D.trains.map(t=>{const ch=t.chemin.map(p=>toM(p[0],p[1])); const cum=[0];
  for(let i=1;i<ch.length;i++)cum.push(cum[i-1]+Math.hypot(ch[i][0]-ch[i-1][0],ch[i][1]-ch[i-1][1]));
  return {...t,ch,cum,len:cum[cum.length-1]};});
function pointA(t,d){d=Math.max(0,Math.min(t.len,d));let i=1;while(i<t.cum.length-1&&t.cum[i]<d)i++;
  const a=t.ch[i-1],b=t.ch[i],f=(d-t.cum[i-1])/Math.max(1e-9,t.cum[i]-t.cum[i-1]);
  return {x:a[0]+f*(b[0]-a[0]),y:a[1]+f*(b[1]-a[1]),cap:Math.atan2(b[1]-a[1],b[0]-a[0])};}
const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
let cx=0,cy=0,s=0.05,mode='mixte',R=700,vit=1,t0=performance.now(),pause=false;
const massy=gares.find(g=>g.nom==='Massy - Palaiseau').p;
function bornes(){let a=[1e9,1e9,-1e9,-1e9];trace.forEach(l=>l.forEach(p=>{a=[Math.min(a[0],p[0]),Math.min(a[1],p[1]),Math.max(a[2],p[0]),Math.max(a[3],p[1])]}));return a;}
function vue(centre,echelle){cx=centre[0];cy=centre[1];s=echelle;}
function toutVoir(){const b=bornes();vue([(b[0]+b[2])/2,(b[1]+b[3])/2],Math.min(cv.width/(b[2]-b[0]),cv.height/(b[3]-b[1]))*.9);}
const px=(x,y)=>[cv.width/2+(x-cx)*s,cv.height/2-(y-cy)*s];
function taille(){const r=cv.getBoundingClientRect(),d=devicePixelRatio||1;cv.width=r.width*d;cv.height=r.height*d;}
function etat(t,now){ // avancement le long du tronçon (m)
  if(t.etat==='a_quai')return 0; if(t.etat==='perime')return t.len*(t.avance||0.5);
  return ((now-t0)/1000*V_REEL*vit)%t.len;}
function dessiner(now){
  const d=devicePixelRatio||1;ctx.clearRect(0,0,cv.width,cv.height);ctx.lineJoin='round';
  ctx.strokeStyle='#8a96a0';ctx.lineWidth=Math.max(1.2,2*d);trace.forEach(l=>{ctx.beginPath();l.forEach((p,i)=>{const q=px(p[0],p[1]);i?ctx.lineTo(q[0],q[1]):ctx.moveTo(q[0],q[1])});ctx.stroke();});
  ctx.font=(11*d)+'px system-ui';gares.forEach(g=>{const q=px(g.p[0],g.p[1]);ctx.fillStyle='#fff';ctx.strokeStyle='#1d2a33';ctx.lineWidth=1.2*d;
    ctx.beginPath();ctx.arc(q[0],q[1],3.2*d,0,7);ctx.fill();ctx.stroke(); if(s>0.02){ctx.fillStyle='#1d2a33';ctx.fillText(g.nom,q[0]+6*d,q[1]-5*d);}});
  const lignes=[];
  trains.forEach(t=>{const p=pointA(t,etat(t,now)),q=px(p.x,p.y),couleur=t.etat==='perime'?'#a0a7ad':(t.etat==='a_quai'?'#2f855a':'#2b6cb0');
    if(mode!=='train'&&R>0){ctx.fillStyle='rgba(214,158,46,.26)';ctx.strokeStyle='#b7791f';ctx.lineWidth=1.5*d;ctx.beginPath();ctx.arc(q[0],q[1],R*s,0,7);ctx.fill();ctx.stroke();}
    if(mode==='zone'){ctx.fillStyle=couleur;ctx.beginPath();ctx.arc(q[0],q[1],4*d,0,7);ctx.fill();}
    else{const longPx=L_RAME*s,largPx=Math.max(W_RAME*s,0);
      ctx.save();ctx.translate(q[0],q[1]);ctx.rotate(-p.cap);
      if(longPx>=14*d){ // rame à l'échelle : 6 caisses, face avant (cabine) côté marche
        const lc=longPx/NB_CAISSES,w=Math.max(largPx,3*d);
        for(let i=0;i<NB_CAISSES;i++){const x0=-longPx/2+i*lc;ctx.fillStyle=t.etat==='perime'?'#c4c9cd':'#f5f2ea';ctx.strokeStyle='#1d2a33';ctx.lineWidth=Math.max(1,.3*d);
          ctx.fillRect(x0+lc*.04,-w/2,lc*.92,w);ctx.strokeRect(x0+lc*.04,-w/2,lc*.92,w);
          if(w>6*d){ctx.fillStyle=t.etat==='perime'?'#a0a7ad':'#2b3a42';ctx.fillRect(x0+lc*.1,-w*.22,lc*.8,w*.44);}}
        ctx.fillStyle=couleur;ctx.fillRect(longPx/2-lc*.18,-w/2,lc*.18,w);          // cabine de tête
      } else { // trop petit : pastille de repère
        ctx.fillStyle=couleur;ctx.strokeStyle='#fff';ctx.lineWidth=1.5*d;ctx.beginPath();ctx.arc(0,0,6*d,0,7);ctx.fill();ctx.stroke();}
      ctx.restore();}
    lignes.push(`${t.nom} : ${t.etat==='a_quai'?'à quai':t.etat==='perime'?'périmé':'en route'}`);});
  document.getElementById('info').innerHTML=lignes.join('<br>')+`<br><span class="note">Rame réelle : ${L_RAME} m ; à ce zoom : ${(L_RAME*s).toFixed(1)} px</span>`;
  const cible=[10,20,50,100,200,500,1000,2000,5000,10000].find(m=>m*s>=70*d)||10000;
  document.getElementById('echelle').innerHTML=`<span style="display:inline-block;width:${cible*s/d}px;border-bottom:3px solid #1d2a33;vertical-align:middle"></span> ${cible>=1000?cible/1000+' km':cible+' m'}`;}
function boucle(now){dessiner(now);requestAnimationFrame(boucle);}
document.querySelectorAll('input[name=mode]').forEach(i=>i.onchange=()=>{mode=i.value;document.querySelectorAll('label.choix').forEach(l=>l.classList.toggle('on',l.querySelector('input').checked));});
document.getElementById('c-mixte').classList.add('on');
const rayon=document.getElementById('rayon');rayon.oninput=()=>{R=+rayon.value;document.getElementById('valR').textContent=R+' m';};
document.querySelectorAll('[data-r]').forEach(b=>b.onclick=()=>{R=+b.dataset.r;rayon.value=R;document.getElementById('valR').textContent=R+' m';});
const vitesse=document.getElementById('vitesse');vitesse.oninput=()=>{vit=+vitesse.value;document.getElementById('valV').textContent=vit;};
document.getElementById('v-ligne').onclick=toutVoir;
document.getElementById('v-massy').onclick=()=>vue([massy[0]+700,massy[1]-300],0.35);
document.getElementById('v-gare').onclick=()=>vue([massy[0]+60,massy[1]-25],5.5);
cv.addEventListener('wheel',e=>{e.preventDefault();const r=cv.getBoundingClientRect(),d=devicePixelRatio||1,mx=(e.clientX-r.left)*d,my=(e.clientY-r.top)*d;
  const wx=cx+(mx-cv.width/2)/s,wy=cy-(my-cv.height/2)/s;s=Math.max(0.004,Math.min(40,s*(e.deltaY<0?1.2:1/1.2)));cx=wx-(mx-cv.width/2)/s;cy=wy+(my-cv.height/2)/s;},{passive:false});
let glisse=null;cv.addEventListener('mousedown',e=>glisse=[e.clientX,e.clientY,cx,cy]);window.addEventListener('mouseup',()=>glisse=null);
window.addEventListener('mousemove',e=>{if(glisse){const d=devicePixelRatio||1;cx=glisse[2]-(e.clientX-glisse[0])*d/s;cy=glisse[3]+(e.clientY-glisse[1])*d/s;}});
window.addEventListener('resize',()=>{taille()});taille();toutVoir();requestAnimationFrame(boucle);
</script></body></html>
"""

if __name__ == "__main__":
    main()
