import { Feed } from "./feed.js";
import { badge, cluster, camIcon, drawCameraFrame, STATE_COLORS } from "./sprites.js";
{ const f = document.createElement("link"); f.rel = "icon"; f.href = "static/spider-mask-transparent.png"; document.head.appendChild(f); }

const $ = id => document.getElementById(id);
const SUBJECT = "T-SUBJ";
const CENTER = [-122.3393, 47.6072]; // 2nd Ave corridor, downtown Seattle
const SPEED_M_PER_S = 233 / 60;   // ~14 km/h scooter, from the prototype
const UNCERT_S = 30;              // sighting-time uncertainty added to the radius
function metres(a, b) { const dx = (b.lon - a.lon) * 111320 * Math.cos(a.lat * Math.PI / 180), dy = (b.lat - a.lat) * 110540; return Math.hypot(dx, dy); }
function circlePoly(lat, lon, r, n = 48) { const c = []; for (let i = 0; i <= n; i++) { const a = i / n * 2 * Math.PI;
  c.push([lon + (r * Math.cos(a)) / (111320 * Math.cos(lat * Math.PI / 180)), lat + (r * Math.sin(a)) / 110540]); }
  return { type: "Feature", geometry: { type: "Polygon", coordinates: [c] }, properties: {} }; }
function searchRadius() { const s = state.lastSubject; if (!s || !state.clock) return 0;
  const age = (state.clock - new Date(s.t).getTime()) / 1000; return age > 8 ? (age + UNCERT_S) * SPEED_M_PER_S : 0; }

// ---------- map: CARTO dark-matter vector style, recolored to blueprint navy ----------
const map = new maplibregl.Map({
  container: "map", center: CENTER, zoom: 14.9, pitch: 0, attributionControl: { compact: true },
  style: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
});
window.map = map; map.on("error", e => (window.__errs ||= []).push("map:" + (e.error?.message || e.error))); map.on("styledata", () => (window.__ev ||= []).push("styledata")); map.on("load", () => (window.__ev ||= []).push("load"));
window.addEventListener("load", () => map.resize()); new ResizeObserver(() => map.resize()).observe(document.getElementById("map"));
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

function recolor() {
  for (const l of map.getStyle().layers) {
    const id = l.id.toLowerCase();
    if (l.type === "background") map.setPaintProperty(l.id, "background-color", "#0A1733");
    else if (l.type === "fill") map.setPaintProperty(l.id, "fill-color", id.includes("water") ? "#061024" : id.includes("building") ? "#10224A" : "#0D1E42");
    else if (l.type === "line") { map.setPaintProperty(l.id, "line-color", id.includes("rail") ? "#1E3E78" : "#2E5FAE"); }
    else if (l.type === "symbol") { map.setPaintProperty(l.id, "text-color", "#7FA6DA"); map.setPaintProperty(l.id, "text-halo-color", "#0A1733"); }
  }
}

// ---------- state ----------
const state = {
  cameras: new Map(), sightings: [], subject: [], lastSubject: null, lostSince: null,
  tracks: new Set(), lostCount: 0, prediction: null, clock: null,
};
const sightingsGeo = () => ({ type: "FeatureCollection", features: state.sightings.map(s => ({
  type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] },
  properties: { id: s.id, state: s.state, conf: s.conf, cls: s.class, track: s.track_id || "", t: s.t, op: 1 } })) });
const camerasGeo = () => { const r = searchRadius(), s = state.lastSubject; return { type: "FeatureCollection", features: [...state.cameras.values()].map(c => ({
  type: "Feature", geometry: { type: "Point", coordinates: [c.lon, c.lat] },
  properties: { id: c.id, name: c.name, alive: c.alive, reach: !!(r && s && c.alive && metres(s, c) <= r && c.id !== s.camera_id) } })) }; };
const reachGeo = () => { const r = searchRadius(), s = state.lastSubject; return { type: "FeatureCollection", features: r ? [circlePoly(s.lat, s.lon, r)] : [] }; };
const subjectLine = () => ({ type: "Feature", geometry: { type: "LineString", coordinates: state.subject.map(s => [s.lon, s.lat]) } });
const predictionGeo = () => ({ type: "FeatureCollection", features: !state.prediction ? [] : state.prediction.branches.map((b, i) => ({
  type: "Feature", geometry: { type: "LineString", coordinates: b.path.map(([la, lo]) => [lo, la]) },
  properties: { p: b.p, label: b.label, actual: state.prediction.actual === b.label, resolved: !!state.prediction.actual, i } })) });
const predictionLabels = () => ({ type: "FeatureCollection", features: !state.prediction ? [] : state.prediction.branches.map(b => {
  const [la, lo] = b.path[b.path.length - 1]; return { type: "Feature", geometry: { type: "Point", coordinates: [lo, la] },
  properties: { label: `${Math.round(b.p*100)}%`, actual: state.prediction.actual === b.label } }; }) });

map.on("load", () => {
  recolor();
  for (const st of Object.keys(STATE_COLORS)) map.addImage(`sight-${st}`, badge(st).data, { pixelRatio: 3 });
  map.addImage("subject-now", badge("linked", 3, true).data, { pixelRatio: 3 });
  map.addImage("cluster", cluster().data, { pixelRatio: 3 });
  map.addImage("cam-alive", camIcon(true).data, { pixelRatio: 3 });
  map.addImage("cam-dead", camIcon(false).data, { pixelRatio: 3 });

  map.addSource("reach", { type: "geojson", data: reachGeo() });
  map.addLayer({ id: "reach-fill", type: "fill", source: "reach", paint: { "fill-color": "#48D06A", "fill-opacity": 0.06 } });
  map.addLayer({ id: "reach-line", type: "line", source: "reach", paint: { "line-color": "#48D06A", "line-opacity": 0.5, "line-width": 1.5, "line-dasharray": [2, 2] } });
  map.addSource("cameras", { type: "geojson", data: camerasGeo() });
  map.addLayer({ id: "cameras-reach", type: "circle", source: "cameras", filter: ["get", "reach"],
    paint: { "circle-radius": 11, "circle-color": "#F6B53A", "circle-opacity": 0.35, "circle-stroke-color": "#F6B53A", "circle-stroke-width": 1.5 } });
  map.addLayer({ id: "cameras", type: "symbol", source: "cameras",
    layout: { "icon-image": ["case", ["get", "alive"], "cam-alive", "cam-dead"], "icon-allow-overlap": true, "icon-size": 1 } });

  map.addSource("subject-line", { type: "geojson", data: subjectLine() });
  map.addLayer({ id: "subject-line", type: "line", source: "subject-line",
    paint: { "line-color": "#F6B53A", "line-width": 3, "line-dasharray": [1, 1.5], "line-opacity": 0.9 } });

  map.addSource("prediction", { type: "geojson", data: predictionGeo() });
  map.addLayer({ id: "prediction-dash", type: "line", source: "prediction", filter: ["!", ["get", "actual"]],
    paint: { "line-color": "#F5E6C4", "line-width": ["+", 2, ["*", 8, ["get", "p"]]], "line-dasharray": [1.2, 1.2],
             "line-opacity": ["case", ["get", "resolved"], 0.25, 0.95] } });
  map.addLayer({ id: "prediction-solid", type: "line", source: "prediction", filter: ["get", "actual"],
    paint: { "line-color": "#48D06A", "line-width": ["+", 2, ["*", 8, ["get", "p"]]], "line-opacity": 0.95 } });
  map.addSource("prediction-labels", { type: "geojson", data: predictionLabels() });
  map.addLayer({ id: "prediction-labels", type: "symbol", source: "prediction-labels",
    layout: { "text-field": ["get", "label"], "text-font": ["Open Sans Bold"], "text-size": 13, "text-offset": [0, -1.2], "text-allow-overlap": true },
    paint: { "text-color": ["case", ["get", "actual"], "#48D06A", "#F5E6C4"], "text-halo-color": "#0A1733", "text-halo-width": 2 } });

  map.addSource("sightings", { type: "geojson", data: sightingsGeo(), cluster: true, clusterRadius: 34, clusterMaxZoom: 15 });
  map.addLayer({ id: "clusters", type: "symbol", source: "sightings", filter: ["has", "point_count"],
    layout: { "icon-image": "cluster", "icon-allow-overlap": true, "text-field": ["get", "point_count_abbreviated"], "text-font": ["Open Sans Bold"], "text-size": 13, "text-allow-overlap": true },
    paint: { "text-color": "#2A1E12" } });
  map.addLayer({ id: "sightings", type: "symbol", source: "sightings", filter: ["!", ["has", "point_count"]],
    layout: { "icon-image": ["concat", "sight-", ["get", "state"]], "icon-allow-overlap": true, "icon-size": 1,
              "symbol-sort-key": ["case", ["==", ["get", "state"], "linked"], 0, 1] },
    paint: { "icon-opacity": ["get", "op"] } });

  map.addSource("subject-now", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({ id: "subject-now", type: "symbol", source: "subject-now",
    layout: { "icon-image": "subject-now", "icon-allow-overlap": true, "icon-size": 1 }, paint: { "icon-opacity": 1 } });

  map.on("click", "clusters", e => { const f = e.features[0];
    map.getSource("sightings").getClusterExpansionZoom(f.properties.cluster_id).then(z => map.easeTo({ center: f.geometry.coordinates, zoom: z })); });
  map.on("click", "sightings", e => { const p = e.features[0].properties;
    new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat).setHTML(`<b>${p.id}</b> ${p.cls} · ${p.state} · ${(+p.conf).toFixed(2)}${p.track ? " · " + p.track : ""}`).addTo(map); });
  map.on("mouseenter", "clusters", () => map.getCanvas().style.cursor = "pointer");
  map.on("mouseleave", "clusters", () => map.getCanvas().style.cursor = "");
  map.on("mouseenter", "cameras", e => { const p = e.features[0].properties; camTip.setLngLat(e.lngLat).setHTML(`${p.id} · ${p.name}${p.alive ? "" : " · DEAD"}`).addTo(map); });
  map.on("mouseleave", "cameras", () => camTip.remove());

  feed.loadReplay("data/sightings.json");
  frame();
});
const HUD_PAD = { top: 90, left: 300, right: 330, bottom: 150 };
function frame(coords) {
  const pts = coords || [...state.cameras.values()].slice(0, 18).map(c => [c.lon, c.lat]);
  if (pts.length < 2) return map.jumpTo({ center: CENTER, zoom: 14.9 });
  const b = pts.reduce((bb, p) => bb.extend(p), new maplibregl.LngLatBounds(pts[0], pts[0]));
  map.fitBounds(b, { padding: HUD_PAD, maxZoom: 15.6, duration: 600 });
}
const camTip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });

function refresh() {
  if (!map.getSource("sightings")) return;
  map.getSource("sightings").setData(sightingsGeo());
  map.getSource("subject-line").setData(subjectLine());
  map.getSource("prediction").setData(predictionGeo());
  map.getSource("prediction-labels").setData(predictionLabels());
  map.getSource("reach").setData(reachGeo());
  map.getSource("cameras").setData(camerasGeo());
  const last = state.lastSubject;
  map.getSource("subject-now").setData({ type: "FeatureCollection", features: last ? [{ type: "Feature", geometry: { type: "Point", coordinates: [last.lon, last.lat] }, properties: {} }] : [] });
}

// ---------- HUD ----------
let tick = 0, camSighting = null, camCamera = null;
function hud() {
  const unlinked = state.sightings.filter(s => !s.track_id && s.state !== "lost").length;
  $("lcdUnlinked").textContent = "0x" + unlinked.toString(16).toUpperCase().padStart(4, "0");
  $("cntSight").textContent = state.sightings.length;
  $("cntTrack").textContent = state.tracks.size;
  $("cntLost").textContent = state.lostCount;
  const alive = [...state.cameras.values()].filter(c => c.alive).length;
  $("lcdCams").textContent = `${alive}/${state.cameras.size}`;
  $("lcdFeed").textContent = feed.ws ? "LIVE" : (feed.playing ? `REPLAY ×${feed.speed}` : "PAUSED");
  if (state.clock) $("lcdClock").textContent = new Date(state.clock).toLocaleTimeString("en-US", { hour12: false });
  const s = state.lastSubject;
  if (s) {
    $("subjCam").textContent = state.cameras.get(s.camera_id)?.name.replace("2nd Ave & ", "2nd & ") || s.camera_id;
    // confidence decays while lost: 0.7%/s of replay clock after last sighting
    const age = state.clock ? (state.clock - new Date(s.t).getTime()) / 1000 : 0;
    const conf = s.state === "lost" || age > 45 ? Math.max(0.05, s.conf - age * 0.007) : s.conf;
    $("subjConf").textContent = conf.toFixed(2);
    $("subjConfBar").style.width = `${conf * 100}%`;
    $("subjConfBar").style.background = conf < 0.4 ? "#F0645A" : "#F6B53A";
    $("subjGap").textContent = age > 8 ? `${Math.round(age)} s since last cam` : "in view";
    const r = searchRadius(); $("subjRadius").textContent = r ? (r >= 1000 ? (r/1000).toFixed(1) + " km" : Math.round(r) + " m") : "—";
    if (map.getSource("reach")) { map.getSource("reach").setData(reachGeo()); const cg = camerasGeo(); map.getSource("cameras").setData(cg);
      $("subjReach").textContent = r ? String(cg.features.filter(f => f.properties.reach).length) : "—"; }
    map.getSource("subject-now")?.setData({ type: "FeatureCollection", features: [{ type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] }, properties: {} }] });
    map.setPaintProperty?.("subject-now", "icon-opacity", Math.max(0.25, conf));
  }
  drawCameraFrame($("camCanvas"), camSighting, camCamera, tick++);
}

let calloutTimer;
function callout(eyebrow, text, color = "#F0645A") {
  $("calloutEyebrow").textContent = eyebrow; $("calloutEyebrow").style.color = color; $("calloutText").innerHTML = text;
  $("callout").hidden = false; clearTimeout(calloutTimer); calloutTimer = setTimeout(() => $("callout").hidden = true, 3500);
}

// ticker: newest event on the left, older ones fade to the right, max 4 visible
const tickerEl = $("ticker");
function tickerAdd(ev) {
  const el = document.createElement("span"); el.className = `tk ${ev.kind}${/lost|dead|closed/i.test(ev.text) ? " warn" : ""}`;
  el.innerHTML = `<span class="kind">${ev.kind.toUpperCase()}</span>${ev.text}`;
  tickerEl.prepend(el);
  while (tickerEl.children.length > 4) tickerEl.lastElementChild.remove();
  [...tickerEl.children].forEach((c, i) => c.style.opacity = String(1 - i * 0.22));
}
function showPrediction(p) {
  $("predictPanel").hidden = false;
  $("predAt").textContent = (state.cameras.get(p.at_camera)?.name || p.at_camera).replace("2nd Ave & ", "2nd & ").replace(" NS", "");
  $("predList").innerHTML = p.branches.map(b => `<li class="${p.actual === b.label ? "actual" : ""}">${b.label}<b>${Math.round(b.p*100)}%</b></li>`).join("");
  $("predActual").textContent = p.actual ? `RESOLVED · ${p.actual.toUpperCase()} · ${p.actual === p.branches[0].label ? "TOP BRANCH CORRECT" : "OFF-TOP-BRANCH"}` : "AWAITING NEXT SIGHTING…";
}

function evalPanel() {
  const gt = feed.data?.ground_truth || []; if (!gt.length) return;
  const tb = $("evalTable").querySelector("tbody"); tb.innerHTML = "";
  let hits = 0, seenWaves = 0;
  const now = state.clock || 0;
  for (const g of gt) {
    const cam = state.cameras.get(g.camera_id); const waveT = new Date(g.t).getTime();
    const found = state.subject.find(x => x.camera_id === g.camera_id && Math.abs(new Date(x.t).getTime() - waveT) < 20000);
    const seen = waveT <= now; if (seen) seenWaves++; if (found) hits++;
    const res = found ? "match" : seen ? (cam && !cam.alive ? "miss·dead cam" : "miss") : "pending";
    tb.insertAdjacentHTML("beforeend", `<tr><td>${(cam?.name || g.camera_id).replace("2nd Ave & ", "2nd & ").replace("2nd Ave S & S ", "2nd & S ")}</td><td>${g.t.slice(11,19)}</td><td>${found ? found.t.slice(11,19) : "—"}</td><td class="${found ? "hit" : seen ? "miss" : "pending"}">${res}</td></tr>`);
  }
  const falsePos = state.subject.filter(x => !gt.some(g => g.camera_id === x.camera_id && Math.abs(new Date(x.t) - new Date(g.t)) < 20000)).length;
  $("evalRecall").textContent = `recall ${hits}/${seenWaves}`;
  $("evalPrec").textContent = `precision ${hits}/${hits + falsePos}`;
}

function camsPanel() {
  const r = searchRadius(), sub = state.lastSubject;
  const lastSeen = new Map(); for (const x of state.sightings) lastSeen.set(x.camera_id, x.t);
  const rows = [...state.cameras.values()].map(c => ({ ...c, reach: !!(r && sub && c.alive && metres(sub, c) <= r && c.id !== sub.camera_id), seen: lastSeen.get(c.id) }))
    .sort((a, b) => (b.reach - a.reach) || (a.alive - b.alive) || (b.seen || "").localeCompare(a.seen || "") || a.name.localeCompare(b.name));
  $("camsCount").textContent = `${rows.filter(c => c.alive).length}/${rows.length} alive`;
  $("camsList").innerHTML = rows.map(c => `<div class="${c.alive ? "" : "dead"}${c.reach ? " reach" : ""}${c.seen ? " seen" : ""}"><span>${c.id}</span><span>${c.name}</span><b>${!c.alive ? "DEAD" : c.reach ? "REACH" : c.seen ? c.seen.slice(11, 19) : "—"}</b></div>`).join("");
}

// ---------- feed wiring ----------
const feed = new Feed();
feed.addEventListener("msg", ({ detail: m }) => {
  switch (m.type) {
    case "reset":
      Object.assign(state, { sightings: [], subject: [], lastSubject: null, tracks: new Set(), lostCount: 0, prediction: null });
      tickerEl.innerHTML = ""; $("predictPanel").hidden = true; camSighting = null; refresh(); break;
    case "camera": state.cameras.set(m.id, m); map.getSource("cameras")?.setData(camerasGeo()); clearTimeout(window.__frameT); window.__frameT = setTimeout(() => frame(), 50); break;
    case "clock": state.clock = m.t; hud(); if (!$("evalPanel").hidden && (tick % 10 === 0)) evalPanel(); if (!$("camsPanel").hidden && (tick % 10 === 0)) camsPanel(); break;
    case "sighting": {
      state.sightings.push(m);
      if (m.track_id) state.tracks.add(m.track_id);
      if (m.state === "lost") state.lostCount++;
      if (m.track_id === SUBJECT) {
        if (m.state === "linked") { state.subject.push(m); callout("SUBJECT SIGHTED", `${state.cameras.get(m.camera_id)?.name || m.camera_id} · ${m.conf.toFixed(2)}`, "#9C6A0C"); }
        else if (m.state === "lost") callout("SUBJECT LOST", "no camera coverage · confidence decaying", "#F0645A");
        state.lastSubject = m;
        // resolve pending prediction when subject reappears
        if (state.prediction && !state.prediction.actual && m.state === "linked" && m.t !== state.prediction.t) {
          const p = feed.data?.predictions.find(x => x.id === state.prediction.id);
          if (p?.actual) { state.prediction = { ...state.prediction, actual: p.actual }; showPrediction(state.prediction); }
        }
      }
      camSighting = m; camCamera = state.cameras.get(m.camera_id);
      $("camId").textContent = m.camera_id; $("camName").textContent = camCamera?.name || "";
      $("camClass").textContent = `${m.class} · ${m.state}`; $("camConf").textContent = `conf ${m.conf.toFixed(2)}`;
      refresh(); break;
    }
    case "event": tickerAdd(m); break;
    case "prediction": state.prediction = { ...m, actual: undefined }; showPrediction(state.prediction); refresh(); break;
    case "frozen": callout("FROZEN AT SPLIT", "which way did the ride go? — press PAUSE to resume", "#2C6BB0"); $("btnPlay").textContent = "PLAY"; break;
    case "done": $("btnPlay").textContent = "REPLAY"; break;
  }
});

// ---------- controls ----------
$("btnPlay").onclick = () => { if (feed.i >= feed.items?.length) return feed.restart(); feed.playing = !feed.playing; $("btnPlay").textContent = feed.playing ? "PAUSE" : "PLAY"; };
$("btnFreeze").onclick = e => { feed.freezeAtSplit = !feed.freezeAtSplit; e.currentTarget.classList.toggle("on", feed.freezeAtSplit); };
$("btnCenter").onclick = () => state.subject.length > 1 ? frame(state.subject.map(s => [s.lon, s.lat])) : frame();
$("btnCluster").onclick = e => { const on = e.currentTarget.classList.toggle("on");
  // rebuild source with clustering toggled
  const data = sightingsGeo(); map.removeLayer("sightings"); map.removeLayer("clusters"); map.removeSource("sightings");
  map.addSource("sightings", { type: "geojson", data, cluster: on, clusterRadius: 34, clusterMaxZoom: 15 });
  map.addLayer({ id: "clusters", type: "symbol", source: "sightings", filter: ["has", "point_count"],
    layout: { "icon-image": "cluster", "icon-allow-overlap": true, "text-field": ["get", "point_count_abbreviated"], "text-font": ["Open Sans Bold"], "text-size": 13, "text-allow-overlap": true }, paint: { "text-color": "#2A1E12" } }, "subject-now");
  map.addLayer({ id: "sightings", type: "symbol", source: "sightings", filter: ["!", ["has", "point_count"]],
    layout: { "icon-image": ["concat", "sight-", ["get", "state"]], "icon-allow-overlap": true, "icon-size": 1 }, paint: { "icon-opacity": ["get", "op"] } }, "subject-now"); };
$("btnEval").onclick = e => { const p = $("evalPanel"); p.hidden = !p.hidden; e.currentTarget.classList.toggle("on", !p.hidden); if (!p.hidden) { $("camsPanel").hidden = true; $("btnCams").classList.remove("on"); evalPanel(); } };
$("btnCams").onclick = e => { const p = $("camsPanel"); p.hidden = !p.hidden; e.currentTarget.classList.toggle("on", !p.hidden); if (!p.hidden) { $("evalPanel").hidden = true; $("btnEval").classList.remove("on"); camsPanel(); } };
$("btnRestart").onclick = () => { feed.restart(); $("btnPlay").textContent = "PAUSE"; };
$("btnSpeed").onclick = e => { const speeds = [1, 3, 6, 12, 30]; feed.speed = speeds[(speeds.indexOf(feed.speed) + 1) % speeds.length]; e.currentTarget.textContent = `×${feed.speed}`; };
document.addEventListener("keydown", e => { if (e.key === " ") { e.preventDefault(); $("btnPlay").click(); } if (e.key === "f") $("btnFreeze").click(); if (e.key === "c") $("btnCenter").click(); });

// live mode: ?ws=ws://gn100-223b:8765
const wsUrl = new URLSearchParams(location.search).get("ws");
if (wsUrl) map.on("load", () => feed.connectLive(wsUrl));
