#!/usr/bin/env python3
"""Build a self-contained interactive knowledge graph HTML from vault data.

Reads _viz_data.json (dumped from mojo.db) and emits a single .html with
all data + CSS + a dependency-free force-directed graph inlined. Opens in
any browser, no network needed.
"""
import json, os, html as _h

DATA = os.path.expanduser("~/wd/projects/mojo/_viz_data.json")
OUT = os.path.expanduser("~/mojo-knowledge-graph.html")

items = json.load(open(DATA, encoding="utf-8"))
data_json = json.dumps(items, ensure_ascii=False)

TPL = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mojo 지식 그래프</title>
<style>
  :root{
    --bg:#fbfbfa; --panel:#ffffff; --ink:#1c1c1e; --muted:#8a8a8e;
    --line:#e6e6e3; --edge:#cfcfca;
    --ml:#4C78A8; --elec:#E8893B; --data:#56A357; --meta:#9aa0a6;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;font-family:-apple-system,"Segoe UI",
    "Noto Sans CJK KR","Malgun Gothic",sans-serif;color:var(--ink);background:var(--bg)}
  #app{display:flex;height:100vh}
  #graph{flex:1;position:relative;overflow:hidden}
  svg{width:100%;height:100%;display:block;cursor:grab}
  svg.dragging{cursor:grabbing}
  .edge{stroke:var(--edge);stroke-width:1}
  .node circle{stroke:#fff;stroke-width:1.5;cursor:pointer;transition:opacity .15s}
  .node text{font-size:9px;fill:var(--muted);pointer-events:none;
    paint-order:stroke;stroke:var(--bg);stroke-width:2.5px}
  .dim{opacity:.12}
  .dim text{opacity:.25}
  /* header */
  header{position:absolute;top:0;left:0;right:0;padding:14px 18px;
    display:flex;align-items:baseline;gap:14px;pointer-events:none}
  header h1{font-size:15px;margin:0;font-weight:650;letter-spacing:-.2px}
  header .sub{font-size:12px;color:var(--muted)}
  /* controls */
  .controls{position:absolute;top:46px;left:18px;display:flex;gap:8px;
    flex-wrap:wrap;align-items:center}
  #search{pointer-events:auto;border:1px solid var(--line);background:#fff;
    border-radius:7px;padding:6px 10px;font-size:12px;width:200px;outline:none}
  #search:focus{border-color:var(--ml)}
  .legend{position:absolute;bottom:14px;left:18px;display:flex;gap:6px;
    flex-wrap:wrap}
  .chip{pointer-events:auto;cursor:pointer;font-size:11px;padding:4px 9px;
    border-radius:20px;border:1px solid var(--line);background:#fff;
    display:flex;align-items:center;gap:6px;user-select:none}
  .chip .dot{width:9px;height:9px;border-radius:50%}
  .chip.off{opacity:.4}
  .hint{position:absolute;bottom:14px;right:18px;font-size:11px;color:var(--muted)}
  /* side panel */
  #panel{width:360px;background:var(--panel);border-left:1px solid var(--line);
    padding:22px;overflow-y:auto;display:flex;flex-direction:column;gap:12px}
  #panel.empty{align-items:center;justify-content:center;color:var(--muted);
    font-size:13px;text-align:center}
  #panel .pgrade{font-size:11px;font-weight:700;padding:2px 7px;border-radius:5px;
    background:#f0f0ee;color:#555;display:inline-block}
  #panel h2{font-size:16px;margin:0;line-height:1.35;letter-spacing:-.2px}
  #panel .meta{font-size:11px;color:var(--muted);display:flex;gap:8px;flex-wrap:wrap}
  #panel .badge{background:#f4f4f2;border-radius:5px;padding:2px 7px}
  #panel section{font-size:13px;line-height:1.6}
  #panel .lbl{font-size:10.5px;font-weight:700;color:var(--muted);
    text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}
  #panel .why{color:#444;background:#faf9f7;border-left:3px solid var(--line);
    padding:8px 11px;border-radius:0 6px 6px 0}
  #panel .tags{display:flex;gap:5px;flex-wrap:wrap}
  #panel .tag{font-size:11px;background:#eef2f6;color:#3a5a78;border-radius:5px;
    padding:2px 7px}
  #panel .rel a{display:block;color:var(--ml);font-size:12px;text-decoration:none;
    padding:3px 0;cursor:pointer}
  #panel .rel a:hover{text-decoration:underline}
  .close{position:absolute;top:16px;right:18px;cursor:pointer;color:var(--muted);
    font-size:18px;line-height:1;border:none;background:none}
</style>
</head>
<body>
<div id="app">
  <div id="graph">
    <header>
      <h1>Mojo 지식 그래프</h1>
      <span class="sub" id="count"></span>
    </header>
    <div class="controls">
      <input id="search" placeholder="검색 (제목·태그·내용)…" autocomplete="off">
    </div>
    <svg id="svg"></svg>
    <div class="legend" id="legend"></div>
    <div class="hint">드래그=이동 · 휠=확대 · 노드 클릭=상세</div>
  </div>
  <div id="panel" class="empty">노드를 클릭하면<br>지식 내용이 여기 표시됩니다</div>
</div>
<script>
const DATA = __DATA__;
const SVGNS="http://www.w3.org/2000/svg";

// ---- domain → top-level group + color ----
const GROUPS=[
  {key:"ml",label:"ML",color:getCSS("--ml"),test:d=>d.startsWith("ml/")},
  {key:"elec",label:"전력/SMP",color:getCSS("--elec"),test:d=>d.startsWith("electricity")},
  {key:"data",label:"데이터",color:getCSS("--data"),test:d=>d.startsWith("data")},
  {key:"meta",label:"도구/워크플로",color:getCSS("--meta"),test:d=>true},
];
function getCSS(v){return getComputedStyle(document.documentElement).getPropertyValue(v).trim();}
function groupOf(domain){return GROUPS.find(g=>g.test(domain||""))||GROUPS[3];}

const byId=new Map(DATA.map(d=>[d.id,d]));
const nodes=DATA.map(d=>({...d, g:groupOf(d.domain)}));
const nodeById=new Map(nodes.map(n=>[n.id,n]));
const links=[];
const seen=new Set();
for(const n of nodes){
  for(const r of (n.related||[])){
    if(!nodeById.has(r)) continue;
    const k=[n.id,r].sort().join("|");
    if(seen.has(k)) continue; seen.add(k);
    links.push({s:n.id,t:r});
  }
}
document.getElementById("count").textContent=`${nodes.length} 지식 · ${links.length} 연결`;

// ---- layout: domain anchors on a circle ----
const svg=document.getElementById("svg");
let W=svg.clientWidth, H=svg.clientHeight;
const anchors={};
GROUPS.forEach((g,i)=>{
  const a=(i/GROUPS.length)*2*Math.PI - Math.PI/2;
  anchors[g.key]={x:W/2+Math.cos(a)*W*0.22, y:H/2+Math.sin(a)*H*0.26};
});
nodes.forEach(n=>{
  const a=anchors[n.g.key]; const j=()=>(Math.random()-.5)*120;
  n.x=a.x+j(); n.y=a.y+j(); n.vx=0; n.vy=0;
});

// ---- force simulation (dependency-free) ----
function tick(){
  // repulsion
  for(let i=0;i<nodes.length;i++){
    const a=nodes[i];
    for(let j=i+1;j<nodes.length;j++){
      const b=nodes[j];
      let dx=a.x-b.x, dy=a.y-b.y; let d2=dx*dx+dy*dy||0.01;
      let f=1600/d2; let d=Math.sqrt(d2);
      dx/=d; dy/=d; a.vx+=dx*f; a.vy+=dy*f; b.vx-=dx*f; b.vy-=dy*f;
    }
  }
  // springs
  for(const l of links){
    const a=nodeById.get(l.s), b=nodeById.get(l.t);
    let dx=b.x-a.x, dy=b.y-a.y; let d=Math.sqrt(dx*dx+dy*dy)||0.01;
    let f=(d-70)*0.02; dx/=d; dy/=d;
    a.vx+=dx*f; a.vy+=dy*f; b.vx-=dx*f; b.vy-=dy*f;
  }
  // cluster gravity to domain anchor
  for(const n of nodes){
    const a=anchors[n.g.key];
    n.vx+=(a.x-n.x)*0.012; n.vy+=(a.y-n.y)*0.012;
    n.vx*=0.82; n.vy*=0.82;
    if(n!==dragNode){ n.x+=n.vx; n.y+=n.vy; }
  }
  render();
  if(alpha>0.02 || dragNode){ alpha*=0.99; requestAnimationFrame(tick); }
}
let alpha=1, dragNode=null;

// ---- render ----
const gEdges=mk("g"), gNodes=mk("g");
svg.append(gEdges,gNodes);
function mk(t){return document.createElementNS(SVGNS,t);}
let view={x:0,y:0,k:1};
function applyView(){ [gEdges,gNodes].forEach(g=>g.setAttribute("transform",
  `translate(${view.x},${view.y}) scale(${view.k})`)); }

const edgeEls=links.map(l=>{const e=mk("line");e.setAttribute("class","edge");
  gEdges.append(e);return e;});
const nodeEls=nodes.map(n=>{
  const g=mk("g"); g.setAttribute("class","node");
  const c=mk("circle"); const r=5+(n.confidence||0.5)*7;
  c.setAttribute("r",r); c.setAttribute("fill",n.g.color);
  const t=mk("text"); t.setAttribute("x",r+3); t.setAttribute("y",3);
  t.textContent=n.title.length>26?n.title.slice(0,25)+"…":n.title;
  g.append(c,t); g._n=n; g._r=r; gNodes.append(g);
  c.addEventListener("click",e=>{e.stopPropagation();select(n);});
  g.addEventListener("mousedown",e=>{e.stopPropagation();startDrag(n,e);});
  c.addEventListener("mouseenter",()=>hover(n));
  c.addEventListener("mouseleave",()=>hover(null));
  return g;
});
function render(){
  for(let i=0;i<links.length;i++){
    const a=nodeById.get(links[i].s), b=nodeById.get(links[i].t);
    const e=edgeEls[i];
    e.setAttribute("x1",a.x);e.setAttribute("y1",a.y);
    e.setAttribute("x2",b.x);e.setAttribute("y2",b.y);
  }
  for(const g of nodeEls) g.setAttribute("transform",`translate(${g._n.x},${g._n.y})`);
}

// ---- interactions ----
const off=new Set();
function visible(n){return !off.has(n.g.key);}
function hover(n){
  if(dragNode) return;
  if(!n){ nodeEls.forEach(g=>g.classList.toggle("dim",!visible(g._n)));
          edgeEls.forEach(e=>e.style.opacity=0.5); return; }
  const nb=new Set([n.id]);
  links.forEach(l=>{if(l.s===n.id)nb.add(l.t); if(l.t===n.id)nb.add(l.s);});
  nodeEls.forEach(g=>g.classList.toggle("dim",!nb.has(g._n.id)));
  edgeEls.forEach((e,i)=>e.style.opacity=(links[i].s===n.id||links[i].t===n.id)?0.9:0.06);
}
function startDrag(n,e){ dragNode=n; svg.classList.add("dragging");
  const move=ev=>{const p=toGraph(ev); n.x=p.x; n.y=p.y; n.vx=n.vy=0; render();};
  const up=()=>{dragNode=null;svg.classList.remove("dragging");
    document.removeEventListener("mousemove",move);document.removeEventListener("mouseup",up);
    alpha=Math.max(alpha,0.3);requestAnimationFrame(tick);};
  document.addEventListener("mousemove",move);document.addEventListener("mouseup",up);
}
function toGraph(ev){const r=svg.getBoundingClientRect();
  return {x:(ev.clientX-r.left-view.x)/view.k,y:(ev.clientY-r.top-view.y)/view.k};}

// pan + zoom on background
let panning=null;
svg.addEventListener("mousedown",e=>{panning={x:e.clientX-view.x,y:e.clientY-view.y};
  svg.classList.add("dragging");});
document.addEventListener("mousemove",e=>{if(!panning)return;
  view.x=e.clientX-panning.x;view.y=e.clientY-panning.y;applyView();});
document.addEventListener("mouseup",()=>{panning=null;svg.classList.remove("dragging");});
svg.addEventListener("wheel",e=>{e.preventDefault();
  const r=svg.getBoundingClientRect();const mx=e.clientX-r.left,my=e.clientY-r.top;
  const k2=Math.min(4,Math.max(0.3,view.k*(e.deltaY<0?1.12:0.89)));
  view.x=mx-(mx-view.x)*(k2/view.k);view.y=my-(my-view.y)*(k2/view.k);view.k=k2;applyView();
},{passive:false});

// legend / filter
const legend=document.getElementById("legend");
GROUPS.forEach(g=>{
  const n=nodes.filter(x=>x.g.key===g.key).length; if(!n)return;
  const c=document.createElement("div");c.className="chip";
  c.innerHTML=`<span class="dot" style="background:${g.color}"></span>${g.label} <span style="color:var(--muted)">${n}</span>`;
  c.onclick=()=>{c.classList.toggle("off");off.has(g.key)?off.delete(g.key):off.add(g.key);applyFilter();};
  legend.append(c);
});
function applyFilter(){
  nodeEls.forEach(g=>g.style.display=visible(g._n)?"":"none");
  edgeEls.forEach((e,i)=>{const a=nodeById.get(links[i].s),b=nodeById.get(links[i].t);
    e.style.display=(visible(a)&&visible(b))?"":"none";});
}

// search
document.getElementById("search").addEventListener("input",e=>{
  const q=e.target.value.trim().toLowerCase();
  if(!q){nodeEls.forEach(g=>g.classList.remove("dim"));return;}
  nodeEls.forEach(g=>{const n=g._n;
    const hay=(n.title+" "+(n.tags||[]).join(" ")+" "+n.content+" "+n.domain).toLowerCase();
    g.classList.toggle("dim",!hay.includes(q));});
});

// detail panel
const panel=document.getElementById("panel");
function esc(s){return (s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}
function select(n){
  panel.className="";
  const rel=(n.related||[]).filter(r=>nodeById.has(r));
  panel.innerHTML=`
    <button class="close">×</button>
    <div><span class="pgrade">${n.grade} · ${esc(n.scope||"")}</span></div>
    <h2>${esc(n.title)}</h2>
    <div class="meta"><span class="badge">${esc(n.domain)}</span>
      <span class="badge">conf ${n.confidence}</span>
      <span class="badge">${esc(n.taxon||"")}</span></div>
    <section>${esc(n.content)}</section>
    ${n.reasoning?`<div><div class="lbl">Why</div><div class="why">${esc(n.reasoning)}</div></div>`:""}
    ${n.applies_when?`<div><div class="lbl">적용 조건</div><section>${esc(n.applies_when)}</section></div>`:""}
    ${(n.tags||[]).length?`<div><div class="lbl">태그</div><div class="tags">${n.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join("")}</div></div>`:""}
    ${rel.length?`<div class="rel"><div class="lbl">연결된 지식</div>${rel.map(r=>`<a data-id="${r}">→ ${esc(byId.get(r).title)}</a>`).join("")}</div>`:""}
    ${n.project?`<div class="meta"><span class="badge">${esc(n.project)}</span></div>`:""}`;
  panel.querySelector(".close").onclick=()=>{panel.className="empty";
    panel.innerHTML="노드를 클릭하면<br>지식 내용이 여기 표시됩니다";hover(null);};
  panel.querySelectorAll(".rel a").forEach(a=>a.onclick=()=>select(nodeById.get(a.dataset.id)));
  hover(n);
}
svg.addEventListener("click",()=>{if(!panning){/* bg click keeps panel */}});

applyView(); hover(null); requestAnimationFrame(tick);
window.addEventListener("resize",()=>{W=svg.clientWidth;H=svg.clientHeight;});
</script>
</body>
</html>"""

html_out = TPL.replace("__DATA__", data_json)
open(OUT, "w", encoding="utf-8").write(html_out)
print(f"wrote {OUT} ({len(html_out)//1024} KB)")
