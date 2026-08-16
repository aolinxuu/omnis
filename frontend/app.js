import { Feed } from "./feed.js";
import { badge, cluster, camIcon, drawCameraFrame, drawDetectionOverlay, drawGlyph, drawRadar, GLYPHS, shieldIcon, STATE_COLORS } from "./sprites.js";
drawGlyph(document.getElementById("badgeGlyph"), GLYPHS.SPIDER, "#1B1410", 4);
drawGlyph(document.getElementById("heartGlyph"), GLYPHS.HEART, "#F26B2B", 4);
drawGlyph(document.getElementById("riderGlyph"), GLYPHS.RIDER, GLYPHS.RIDER_COLORS, 3);
{ const f = document.createElement("link"); f.rel = "icon"; f.href = "static/spider-mask-transparent.png"; document.head.appendChild(f); }

const $ = id => document.getElementById(id);
const isSubject = id => !!id && id.startsWith("T-SUBJ");
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
  cameras: new Map(), sightings: [], subject: [], subjectByTrack: new Map(), lastSubject: null, lostSince: null,
  tracks: new Set(), lostCount: 0, prediction: null, clock: null,
};
const sightingsGeo = () => ({ type: "FeatureCollection", features: state.sightings.map(s => ({
  type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] },
  properties: { id: s.id, state: s.state, conf: s.conf, cls: s.class, track: s.track_id || "", t: s.t, op: 1 } })) });
const camerasGeo = () => { const r = searchRadius(), s = state.lastSubject; return { type: "FeatureCollection", features: [...state.cameras.values()].map(c => ({
  type: "Feature", geometry: { type: "Point", coordinates: [c.lon, c.lat] },
  properties: { id: c.id, name: c.name, alive: c.alive, kind: c.kind || "sdot", image: c.image || "", stream: c.stream || "", reach: !!(r && s && c.alive && metres(s, c) <= r && c.id !== s.camera_id) } })) }; };
const reachGeo = () => { const r = searchRadius(), s = state.lastSubject; return { type: "FeatureCollection", features: r ? [circlePoly(s.lat, s.lon, r)] : [] }; };
const subjectLine = () => ({ type: "Feature", geometry: { type: "MultiLineString", coordinates: [...state.subjectByTrack.values()].map(arr => arr.map(s => [s.lon, s.lat])).filter(a => a.length > 1) } });
const predictionGeo = () => ({ type: "FeatureCollection", features: !state.prediction ? [] : state.prediction.branches.map((b, i) => ({
  type: "Feature", geometry: { type: "LineString", coordinates: b.path.map(([la, lo]) => [lo, la]) },
  properties: { p: b.p, label: b.label, actual: state.prediction.actual === b.label, resolved: !!state.prediction.actual, i } })) });
const predictionLabels = () => ({ type: "FeatureCollection", features: !state.prediction ? [] : state.prediction.branches.map(b => {
  const [la, lo] = b.path[b.path.length - 1]; return { type: "Feature", geometry: { type: "Point", coordinates: [lo, la] },
  properties: { label: `${Math.round(b.p*100)}%`, actual: state.prediction.actual === b.label } }; }) });

let layersReady = false;
function initLayers() {
  if (layersReady) return; layersReady = true;
  try { recolor();
  for (const st of Object.keys(STATE_COLORS)) map.addImage(`sight-${st}`, badge(st).data, { pixelRatio: 3 });
  map.addImage("subject-now", badge("linked", 3, true).data, { pixelRatio: 3 });
  map.addImage("cluster", cluster().data, { pixelRatio: 3 });
  map.addImage("cam-alive", camIcon(true).data, { pixelRatio: 3 });
  map.addImage("cam-dead", camIcon(false).data, { pixelRatio: 3 });
  map.addImage("cam-wsdot", camIcon(true, "wsdot").data, { pixelRatio: 3 });

  map.addSource("reach", { type: "geojson", data: reachGeo() });
  map.addLayer({ id: "reach-fill", type: "fill", source: "reach", paint: { "fill-color": "#48D06A", "fill-opacity": 0.06 } });
  map.addLayer({ id: "reach-line", type: "line", source: "reach", paint: { "line-color": "#48D06A", "line-opacity": 0.5, "line-width": 1.5, "line-dasharray": [2, 2] } });
  map.addSource("cameras", { type: "geojson", data: camerasGeo() });
  map.addLayer({ id: "cameras-reach", type: "circle", source: "cameras", filter: ["get", "reach"],
    paint: { "circle-radius": 11, "circle-color": "#F6B53A", "circle-opacity": 0.35, "circle-stroke-color": "#F6B53A", "circle-stroke-width": 1.5 } });
  map.addLayer({ id: "cameras", type: "symbol", source: "cameras",
    layout: { "icon-image": ["case", ["!", ["get", "alive"]], "cam-dead", ["==", ["get", "kind"], "wsdot"], "cam-wsdot", "cam-alive"], "icon-allow-overlap": true,
              "icon-size": ["interpolate", ["linear"], ["zoom"], 9, 0.55, 12, 0.8, 14, 1, 17, 1.4] } });

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

  map.addImage("shield", shieldIcon().data, { pixelRatio: 3 });
  map.addSource("dispatch", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({ id: "dispatch", type: "symbol", source: "dispatch",
    layout: { "icon-image": "shield", "icon-allow-overlap": true, "icon-size": 1.1, "text-field": ["get", "unit"], "text-font": ["Open Sans Bold"], "text-size": 11, "text-offset": [0, 1.6], "text-allow-overlap": true },
    paint: { "text-color": "#CFE4FF", "text-halo-color": "#0A1733", "text-halo-width": 2 } });

  map.on("click", "clusters", e => { const f = e.features[0];
    map.getSource("sightings").getClusterExpansionZoom(f.properties.cluster_id).then(z => map.easeTo({ center: f.geometry.coordinates, zoom: z })); });
  map.on("click", "sightings", e => { const p = e.features[0].properties;
    new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat).setHTML(`<b>${p.id}</b> ${p.cls} · ${p.state} · ${(+p.conf).toFixed(2)}${p.track ? " · " + p.track : ""}`).addTo(map); });
  map.on("mouseenter", "clusters", () => map.getCanvas().style.cursor = "pointer");
  map.on("mouseleave", "clusters", () => map.getCanvas().style.cursor = "");
  map.on("mouseenter", "cameras", e => { map.getCanvas().style.cursor = "pointer"; if (!camPinned) openCamPopup(e.features[0].properties, e.lngLat, false); });
  map.on("mouseleave", "cameras", () => { map.getCanvas().style.cursor = ""; if (!camPinned) closeCamPopup(); });
  map.on("click", "cameras", e => { camPinned = true; openCamPopup(e.features[0].properties, e.lngLat, true); });
  map.on("click", e => { if (!map.queryRenderedFeatures(e.point, { layers: ["cameras"] }).length && camPinned) { camPinned = false; closeCamPopup(); } });

  refresh(); frameCurrent();
  } catch (e) { layersReady = false; (window.__errs ||= []).push("init: " + (e.stack || e)); console.error(e); }
}
map.on("load", initLayers);
const readyPoll = setInterval(() => { if (layersReady) return clearInterval(readyPoll); if (map.isStyleLoaded()) initLayers(); else map.triggerRepaint(); }, 250);
map.once("styledata", () => setTimeout(() => { if (!layersReady && map.isStyleLoaded()) initLayers(); }, 0));
const HUD_PAD = { top: 90, left: 300, right: 330, bottom: 150 };
function frameCurrent() {
  const tid = state.lastSubject?.track_id;
  const ride = (feed.data?.meta?.rides || []).find(r => r.track === tid);
  const ids = ride?.cameras || feed.data?.meta?.corridor_cameras || [];
  const pts = ids.map(id => state.cameras.get(id)).filter(Boolean).map(c => [c.lon, c.lat]);
  frame(pts.length > 1 ? pts : undefined);
}
function frame(coords) {
  const ids = feed.data?.meta?.corridor_cameras;
  const pts = coords || (ids ? ids.map(id => state.cameras.get(id)).filter(Boolean) : [...state.cameras.values()].slice(0, 18)).map(c => [c.lon, c.lat]);
  if (pts.length < 2) return map.jumpTo({ center: CENTER, zoom: 14.9 });
  const b = pts.reduce((bb, p) => bb.extend(p), new maplibregl.LngLatBounds(pts[0], pts[0]));
  map.fitBounds(b, { padding: HUD_PAD, maxZoom: 15.6, duration: 600 });
}
// ---------- camera hover/pin popup: live still, then HLS video ----------
const camTip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10, maxWidth: "360px" });
let camPinned = false, camHls = null;
let camStillTimer = null, camHlsTimer = null;
function closeCamPopup() { if (camHls) { camHls.destroy(); camHls = null; } clearInterval(camStillTimer); clearTimeout(camHlsTimer); camTip.remove(); }
function openCamPopup(p, lngLat, pinned) {
  closeCamPopup();
  const cam = state.cameras.get(p.id) || p; const alive = cam.alive !== false && cam.alive !== "false";
  const stillUrl = () => `${cam.image}?t=${Date.now()}`;
  const img = cam.image ? `<img src="${stillUrl()}" alt="">` : "";
  camTip.setLngLat(lngLat).setHTML(`<div class="cam-pop">
    <div class="hd"><span class="id">${cam.id}</span><span class="st ${alive ? "" : "dead"}">${alive ? "ALIVE" : "DEAD"}</span></div>
    <div class="name">${cam.name}</div>
    <div class="view">${img}<video muted playsinline autoplay></video><span class="badge" ${cam.image ? "hidden" : ""}>${cam.image ? "" : "NO FEED"}</span></div>
    <div class="hint">${pinned ? "pinned · click map to close" : "click camera to pin"}</div></div>`).addTo(map);
  const el = camTip.getElement(), video = el.querySelector("video"), badge = el.querySelector(".badge"), still = el.querySelector("img");
  if (still) camStillTimer = setInterval(() => { if (!video.classList.contains("on")) still.src = stillUrl(); }, 4000);
  if (cam.kind === "wsdot" || !alive || !cam.stream) return;
  // dwell before starting HLS so sweeping across cameras doesn't hammer the stream server
  camHlsTimer = setTimeout(() => {
    const giveUp = () => { if (camHls) { camHls.destroy(); camHls = null; } };   // stay on the still, say nothing
    const deadline = setTimeout(() => { if (!video.classList.contains("on")) giveUp(); }, 6000);
    const onPlaying = () => { clearTimeout(deadline); video.classList.add("on"); badge.hidden = false; badge.textContent = "LIVE · HLS"; badge.classList.add("live"); };
    video.addEventListener("playing", onPlaying, { once: true });
    if (window.Hls && Hls.isSupported()) {
      camHls = new Hls({ liveSyncDurationCount: 2, maxBufferLength: 10, manifestLoadingTimeOut: 4000, manifestLoadingMaxRetry: 0, levelLoadingTimeOut: 4000, levelLoadingMaxRetry: 0, fragLoadingTimeOut: 6000 });
      camHls.loadSource(cam.stream); camHls.attachMedia(video);
      camHls.on(Hls.Events.ERROR, (_, d) => { if (d.fatal) { clearTimeout(deadline); giveUp(); } });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) { video.src = cam.stream; }
  }, 400);
}

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
// ---- camera tile: live still (+HLS after dwell) of the current sighting's camera, bbox drawn on top ----
let tileCam = null, tileHls = null, tileStillTimer = null, tileHlsTimer = null;
function tileFeed(cam) {
  if (!cam || cam.id === tileCam?.id) return;
  tileCam = cam;
  const img = $("camStill"), video = $("camVideo"), badge = $("camBadge"), cv = $("camCanvas");
  if (tileHls) { tileHls.destroy(); tileHls = null; } clearInterval(tileStillTimer); clearTimeout(tileHlsTimer);
  video.classList.remove("on"); video.removeAttribute("src"); badge.classList.remove("live");
  badge.hidden = true; badge.textContent = "";
  if (!cam.image) { img.removeAttribute("src"); img.style.display = "none"; cv.classList.add("synthetic"); return; }
  img.style.display = ""; cv.classList.remove("synthetic");
  const stillUrl = () => `${cam.image}?t=${Date.now()}`;
  img.src = stillUrl();
  tileStillTimer = setInterval(() => { if (!video.classList.contains("on")) img.src = stillUrl(); }, 4000);
  if (cam.kind === "wsdot" || !cam.stream || cam.alive === false) return;
  tileHlsTimer = setTimeout(() => {
    // silent while trying: the still is on screen; only announce once video is really playing
    const giveUp = () => { if (tileHls) { tileHls.destroy(); tileHls = null; } };
    const deadline = setTimeout(() => { if (!video.classList.contains("on")) giveUp(); }, 6000);
    video.addEventListener("playing", () => { clearTimeout(deadline); video.classList.add("on"); badge.hidden = false; badge.textContent = "LIVE · HLS"; badge.classList.add("live"); }, { once: true });
    if (window.Hls && Hls.isSupported()) {
      tileHls = new Hls({ liveSyncDurationCount: 2, maxBufferLength: 10, manifestLoadingTimeOut: 4000, manifestLoadingMaxRetry: 0, levelLoadingTimeOut: 4000, levelLoadingMaxRetry: 0 });
      tileHls.loadSource(cam.stream); tileHls.attachMedia(video);
      tileHls.on(Hls.Events.ERROR, (_, d) => { if (d.fatal) { clearTimeout(deadline); giveUp(); } });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) video.src = cam.stream;
  }, 1500);
}
function hud() {
  const unlinked = state.sightings.filter(s => !s.track_id && s.state !== "lost").length;
  $("lcdUnlinked").textContent = "0x" + unlinked.toString(16).toUpperCase().padStart(8, "0");
  $("heartCount").textContent = state.subject.length;
  $("cntSight").textContent = state.sightings.length;
  $("cntTrack").textContent = state.tracks.size;
  $("cntLost").textContent = state.lostCount;
  const alive = [...state.cameras.values()].filter(c => c.alive).length;
  $("lcdCams").textContent = `${alive}/${state.cameras.size}`;
  $("lcdFeed").textContent = feed.ws ? "LIVE" : (feed.playing ? `REPLAY ×${feed.speed}` : "PAUSED");
  if (state.clock) $("lcdClock").textContent = new Date(state.clock).toLocaleTimeString("en-US", { hour12: false });
  const s = state.lastSubject;
  if (s) {
    $("subjCam").textContent = (state.cameras.get(s.camera_id)?.name || s.camera_id).replace("2nd Ave & ", "2nd & ").replace("Westlake Ave N & ", "Westlake & ").replace("Westlake Ave & ", "Westlake & ");
    $("subjHdr").textContent = `${s.track_id} · PRESENTER · CONSENTED`;
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
    if (map.getLayer("subject-now")) map.setPaintProperty("subject-now", "icon-opacity", Math.max(0.25, conf));
  }
  if (camCamera?.image) drawDetectionOverlay($("camCanvas"), camSighting); else drawCameraFrame($("camCanvas"), camSighting, camCamera, tick);
  tick++;
  // radar + reticle follow the subject
  const sub = state.lastSubject;
  const pts = state.sightings.slice(-120).map(x => ({ lat: x.lat, lon: x.lon, color: STATE_COLORS[x.state]?.fill }));
  drawRadar($("radar"), sub ? { lat: sub.lat, lon: sub.lon } : null, pts, 900, (tick % 120) / 120 * Math.PI * 2);
  if (sub && map.loaded && map.getContainer()) {
    const p = map.project([sub.lon, sub.lat]);
    $("retH").setAttribute("x1", 0); $("retH").setAttribute("x2", window.innerWidth); $("retH").setAttribute("y1", p.y); $("retH").setAttribute("y2", p.y);
    $("retV").setAttribute("y1", 0); $("retV").setAttribute("y2", window.innerHeight); $("retV").setAttribute("x1", p.x); $("retV").setAttribute("x2", p.x);
    $("retC").setAttribute("cx", p.x); $("retC").setAttribute("cy", p.y);
  }
  $("radarWrap").style.visibility = $("predictPanel").hidden ? "visible" : "hidden";
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
  $("camsList").innerHTML = rows.map(c => `<div class="${c.alive ? "" : "dead"}${c.reach ? " reach" : ""}${c.seen ? " seen" : ""}"><span>${c.id}</span><span>${c.kind === "wsdot" ? "<i>W</i> " : ""}${c.name}</span><b>${!c.alive ? "DEAD" : c.reach ? "REACH" : c.seen ? c.seen.slice(11, 19) : "—"}</b></div>`).join("");
}

// ---------- feed wiring ----------
const feed = new Feed();
feed.loadReplay("data/sightings.json");
feed.addEventListener("msg", ({ detail: m }) => {
  switch (m.type) {
    case "reset":
      Object.assign(state, { sightings: [], subject: [], subjectByTrack: new Map(), lastSubject: null, tracks: new Set(), lostCount: 0, prediction: null });
      tickerEl.innerHTML = ""; $("predictPanel").hidden = true; $("dispatchPanel").hidden = true; map.getSource("dispatch")?.setData({ type: "FeatureCollection", features: [] }); camSighting = null; refresh(); break;
    case "camera": state.cameras.set(m.id, m); map.getSource("cameras")?.setData(camerasGeo()); clearTimeout(window.__frameT); window.__frameT = setTimeout(() => frameCurrent(), 50); break;
    case "clock": state.clock = m.t; hud(); if (!$("evalPanel").hidden && (tick % 10 === 0)) evalPanel(); if (!$("camsPanel").hidden && (tick % 10 === 0)) camsPanel(); break;
    case "sighting": {
      state.sightings.push(m);
      if (m.track_id) state.tracks.add(m.track_id);
      if (m.state === "lost") state.lostCount++;
      if (isSubject(m.track_id)) {
        if (m.state === "linked") { const bb = $("badgeBtn"); bb.classList.remove("alert"); void bb.offsetWidth; bb.classList.add("alert"); state.subject.push(m); if (!state.subjectByTrack.has(m.track_id)) state.subjectByTrack.set(m.track_id, []); state.subjectByTrack.get(m.track_id).push(m); callout("SUBJECT SIGHTED", `${state.cameras.get(m.camera_id)?.name || m.camera_id} · ${m.conf.toFixed(2)}`, "#9C6A0C"); }
        else if (m.state === "lost") callout("SUBJECT LOST", "no camera coverage · confidence decaying", "#F0645A");
        const newRide = !state.lastSubject || state.lastSubject.track_id !== m.track_id;
        state.lastSubject = m;
        if (m.state === "linked") setTimeout(() => requestPrediction(m.track_id), 800);   // give a feed-supplied prediction first dibs
        if (newRide) setTimeout(frameCurrent, 300);
        // resolve pending prediction when subject reappears
        if (state.prediction && !state.prediction.actual && m.state === "linked" && m.t !== state.prediction.t) {
          const p = feed.data?.predictions.find(x => x.id === state.prediction.id);
          if (p?.actual) { state.prediction = { ...state.prediction, actual: p.actual }; showPrediction(state.prediction); if (!$("dispatchPanel").hidden) showDispatch(); }
        }
      }
      camSighting = m; camCamera = state.cameras.get(m.camera_id); tileFeed(camCamera);
      $("camId").textContent = m.camera_id; $("camName").textContent = camCamera?.name || "";
      $("camClass").textContent = `${m.class} · ${m.state}`; $("camConf").textContent = `conf ${m.conf.toFixed(2)}`;
      refresh(); break;
    }
    case "event": tickerAdd(m); break;
    case "prediction": state.prediction = { ...m, actual: undefined }; showPrediction(state.prediction); refresh(); if (!$("dispatchPanel").hidden) showDispatch(); break;
    case "frozen": callout("FROZEN AT SPLIT", "which way did the ride go? — press PAUSE to resume", "#2C6BB0"); $("btnPlay").textContent = "PLAY"; break;
    case "done": $("btnPlay").textContent = "REPLAY"; break;
  }
});

// ---------- ⌘K palette: describe suspect / vehicle → search ----------
let API = new URLSearchParams(location.search).get("api") || localStorage.getItem("omnisApi") || "";
function setApi(url) { API = (url || "").trim(); if (API) localStorage.setItem("omnisApi", API); else localStorage.removeItem("omnisApi");
  $("paletteMode").textContent = API ? `LIVE · ${API}` : "DEMO MATCH · no query server (/api http://spark:8765)";
  $("paletteMode").classList.toggle("live", !!API); }
setApi(API);
// auto-detect the Spark query server so nobody has to remember ?api=; /api off disables
const DEFAULT_API = "http://gn100-223b:8765";
if (!API && localStorage.getItem("omnisApiOff") !== "1") {
  fetch(`${DEFAULT_API}/cameras`, { signal: AbortSignal.timeout(2500) }).then(r => r.ok ? r.json() : null)
    .then(j => { if (j) { setApi(DEFAULT_API); tickerAdd({ kind: "system", text: `query server found at ${DEFAULT_API} · ${(j.cameras || []).length} clip(s) searchable · ⌘K` }); } })
    .catch(() => {});
}
let paletteOpen = false;
function openPalette() { paletteOpen = true; $("palette").hidden = false; $("paletteInput").focus(); $("paletteInput").select(); }
function closePalette() { paletteOpen = false; $("palette").hidden = true; }
document.addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); paletteOpen ? closePalette() : openPalette(); }
  else if (e.key === "Escape" && paletteOpen) closePalette();
});
$("palette").addEventListener("click", e => { if (e.target === $("palette")) closePalette(); });
document.querySelectorAll(".palette-hints button").forEach(b => b.onclick = () => { $("paletteInput").value = b.dataset.q; runPalette(b.dataset.q); });
$("paletteInput").addEventListener("keydown", e => { if (e.key === "Enter") runPalette($("paletteInput").value.trim()); });

const STOP = new Set("a an the in on at of and or with is are was were to for by from into near person people someone driving riding wearing suspect vehicle".split(" "));
const tokens = q => q.toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter(w => w && !STOP.has(w));

function demoMatch(q) {
  // no VSS query server configured: match the description against what the replay already knows
  const ws = tokens(q); if (!ws.length) return [];
  const evs = feed.data?.events || [];
  const scored = state.sightings.map(s => {
    const cam = state.cameras.get(s.camera_id);
    const near = evs.filter(e => e.camera_id === s.camera_id && Math.abs(new Date(e.t) - new Date(s.t)) < 90000).map(e => e.text).join(" ");
    const hay = `${s.note || ""} ${s.class} ${s.state} ${cam?.name || ""} ${near}`.toLowerCase();
    const hit = ws.filter(w => hay.includes(w)).length;
    return { s, cam, score: hit / ws.length, detail: (s.note || near || `${s.class} · ${s.state}`).slice(0, 110) };
  }).filter(r => r.score > 0).sort((a, b) => b.score - a.score || new Date(b.s.t) - new Date(a.s.t));
  return scored.slice(0, 12).map(r => ({ camera_id: r.s.camera_id, camera_name: r.cam?.name || r.s.camera_id, lat: r.s.lat, lon: r.s.lon,
    t: r.s.t, detail: r.detail, score: r.score, image: r.cam?.image, sighting_id: r.s.id }));
}

async function liveQuery(q, onProgress) {
  // NDJSON stream: one line per camera as the VLM answers, then a final {complete:true}
  const r = await fetch(`${API.replace(/\/$/, "")}/query?q=${encodeURIComponent(q)}&stream=1`);
  if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
  const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "", final = null;
  while (true) {
    const { value, done } = await reader.read(); if (done) break;
    buf += dec.decode(value, { stream: true });
    let i; while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (!line) continue;
      const j = JSON.parse(line);
      if (j.complete) { final = j; if (j.error) throw new Error(j.error); }
      else if (onProgress) onProgress(j);
    }
  }
  return ((final && final.results) || []).map(h => ({ ...h, score: 1, image: state.cameras.get(h.camera_id)?.image }));
}

function collect(box) { return [...box.querySelectorAll(".pr[data-hit]")].map(el => JSON.parse(el.dataset.hit)); }
function renderResults(q, hits, mode) {
  const box = $("paletteResults"); box.innerHTML = "";
  if (!hits.length) { box.innerHTML = `<div class="pr empty">no matching sightings for "${q}"${mode === "demo" ? " (demo matcher searches notes + VSS captions of the loaded replay)" : ""}</div>`; return; }
  hits.forEach(h => {
    const el = document.createElement("div"); el.className = "pr"; el.dataset.hit = JSON.stringify(h);
    el.innerHTML = `${h.image ? `<img src="${h.image}?t=${Date.now() >> 12}" alt="">` : "<span></span>"}<div><div class="t">${h.t.slice(11, 19)} · ${h.camera_name}</div><div class="d">${h.detail}</div></div><span class="s">${Math.round((h.score ?? 1) * 100)}%</span>`;
    el.onclick = () => { closePalette(); map.easeTo({ center: [h.lon, h.lat], zoom: 16 }); callout("QUERY HIT", `${h.camera_name} · ${h.t.slice(11, 19)}`, "#2C6BB0"); };
    box.appendChild(el);
  });
}

async function runPalette(q) {
  if (!q) return;
  if (q === "/dispatch") { closePalette(); showDispatch(true); return; }
  if (q === "/api off") { localStorage.setItem("omnisApiOff", "1"); setApi(""); $("paletteResults").innerHTML = `<div class="pr empty">live search disabled — demo match</div>`; return; }
  if (q.startsWith("/api")) { localStorage.removeItem("omnisApiOff"); setApi(q.slice(4)); $("paletteResults").innerHTML = `<div class="pr empty">${API ? "query server set to " + API : "query server cleared — demo match"}</div>`; return; }
  if (q === "/clear") { $("dispatchPanel").hidden = true; map.getSource("dispatch")?.setData({ type: "FeatureCollection", features: [] }); closePalette(); return; }
  $("paletteResults").innerHTML = `<div class="pr empty">searching ${API ? "VSS on the Spark" : "the loaded replay"} for "${q}"…</div>`;
  let hits = [], mode = API ? "live" : "demo";
  const progress = j => { const box = $("paletteResults");
    if (j.done === 1) box.innerHTML = "";
    if (j.hit) { const h = { ...j.hit, score: 1, image: state.cameras.get(j.hit.camera_id)?.image }; renderResults(q, [...collect(box), h], "live"); }
    box.insertAdjacentHTML("beforeend", `<div class="pr empty progress">${j.done}/${j.total} cameras asked · ${j.camera_id} ${j.hit ? "✔ hit" : "— no match"}</div>`);
    box.querySelectorAll(".progress").forEach((el, i, all) => { if (i < all.length - 1) el.remove(); }); };
  try { hits = API ? await liveQuery(q, progress) : demoMatch(q); }
  catch (e) { $("paletteResults").innerHTML = `<div class="pr empty">query server error: ${e.message}</div>`; return; }
  renderResults(q, hits, mode);
  tickerAdd({ kind: "system", text: `query "${q}" → ${hits.length} hit${hits.length === 1 ? "" : "s"}${mode === "demo" ? " (demo match)" : ""}` });
  // put the hits on the map as sightings of a query track
  const src = map.getSource("sightings"); if (!src) return;
  const now = new Date().toISOString();
  hits.forEach((h, i) => { if (!state.sightings.some(s => s.id === `Q-${h.sighting_id || i}-${h.camera_id}`))
    state.sightings.push({ id: `Q-${h.sighting_id || i}-${h.camera_id}`, t: h.t || now, camera_id: h.camera_id, lat: h.lat, lon: h.lon, class: "scooter", state: "confirmed", conf: h.score ?? 1, track_id: "T-QUERY", note: h.detail }); });
  refresh();
}

// ---------- live prediction: ask the Spark's routing engine after each subject sighting ----------
let predictBusy = false;
async function requestPrediction(trackId) {
  if (!API || predictBusy) return;
  const seq = state.subjectByTrack.get(trackId) || []; if (seq.length < 2) return;
  const last = seq[seq.length - 1];
  if (state.prediction && state.prediction.at_camera === last.camera_id && state.prediction.track_id === trackId) return;
  predictBusy = true;
  try {
    // send the roster the page knows (646 cams) so the server can predict anywhere in the city
    const roster = [...state.cameras.values()].map(c => ({ id: c.id, name: c.name, lat: c.lat, lon: c.lon, alive: c.alive !== false }));
    const r = await fetch(`${API.replace(/\/$/, "")}/predict`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sightings: seq.slice(-6), cameras: roster, horizon_s: 180 }) });
    const j = await r.json();
    if (j.prediction) { state.prediction = { ...j.prediction, actual: undefined }; showPrediction(state.prediction); refresh(); if (!$("dispatchPanel").hidden) showDispatch();
      tickerAdd({ kind: "system", text: `route split from the Spark at ${state.cameras.get(j.prediction.at_camera)?.name || j.prediction.at_camera} · leading ${Math.round(j.prediction.branches[0].p * 100)}%` }); }
  } catch (e) { console.warn("predict failed", e); }
  finally { predictBusy = false; }
}

// ---------- dispatch: where units should be, from the current prediction ----------
function showDispatch(force = false) {
  const p = state.prediction; const panel = $("dispatchPanel");
  if (!p) { if (force) { if (API && state.lastSubject) { requestPrediction(state.lastSubject.track_id).then(() => state.prediction && showDispatch(true)); callout("ASKING THE SPARK", "computing the route split for dispatch…", "#2C6BB0"); }
      else callout("NO PREDICTION YET", "dispatch needs a route split — wait for the next subject sighting", "#8E2C24"); } return; }
  const v = p.speed_mps || 4.0;
  const rows = p.branches.map((b, i) => {
    const end = b.path[b.path.length - 1];
    let dist = b.distance_m; if (dist == null) { dist = 0; for (let k = 1; k < b.path.length; k++) dist += metres({ lat: b.path[k-1][0], lon: b.path[k-1][1] }, { lat: b.path[k][0], lon: b.path[k][1] }); }
    const eta = b.eta_s ?? Math.round(dist / v);
    return { unit: `UNIT ${String.fromCharCode(65 + i)}`, label: b.label.split(" via ")[0], p: b.p, eta, lat: end[0], lon: end[1], actual: p.actual === b.label };
  });
  panel.hidden = false;
  $("dispatchList").innerHTML = rows.map(r => `<li class="${r.actual ? "actual" : ""}"><span class="unit">${r.unit}</span>${r.label}<b>${Math.round(r.p*100)}% · ETA ${r.eta}s</b></li>`).join("");
  $("dispatchNote").textContent = `HOLD ONE UNIT AT ${state.cameras.get(p.at_camera)?.name || p.at_camera} · POSITIONS = PREDICTED NEXT CAMERAS, ORDERED BY PROBABILITY · NO LIVE POLICE FEED EXISTS; THIS IS OUR RECOMMENDATION`;
  map.getSource("dispatch")?.setData({ type: "FeatureCollection", features: rows.map(r => ({ type: "Feature", geometry: { type: "Point", coordinates: [r.lon, r.lat] }, properties: { unit: r.unit } })) });
}

// ---------- controls ----------
$("btnPlay").onclick = () => { if (feed.i >= feed.items?.length) return feed.restart(); feed.playing = !feed.playing; $("btnPlay").textContent = feed.playing ? "PAUSE" : "PLAY"; };
$("btnFreeze").onclick = e => { feed.freezeAtSplit = !feed.freezeAtSplit; e.currentTarget.classList.toggle("on", feed.freezeAtSplit); };
$("btnCenter").onclick = () => { const cur = state.lastSubject && state.subjectByTrack.get(state.lastSubject.track_id); cur && cur.length > 1 ? frame(cur.map(s => [s.lon, s.lat])) : frame(); };
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
document.querySelectorAll(".chip-btn[data-ride]").forEach(b => b.onclick = () => {
  const ride = (feed.data?.meta?.rides || [])[+b.dataset.ride]; if (!ride) return;
  document.querySelectorAll(".chip-btn[data-ride]").forEach(x => x.classList.toggle("on", x === b));
  const ids = ride.cameras || []; frame(ids.map(id => state.cameras.get(id)).filter(Boolean).map(c => [c.lon, c.lat]));
  callout("RIDE " + (+b.dataset.ride + 1), ride.label.split(":")[0], "#3A0E0A");
});
$("btnMore").onclick = e => { document.body.classList.toggle("hud-lite"); e.currentTarget.classList.toggle("on", !document.body.classList.contains("hud-lite")); };
$("btnMore").classList.add("on");
setTimeout(() => tickerAdd({ kind: "system", text: "press ⌘K / Ctrl+K to describe a suspect or vehicle · /dispatch for unit positions" }), 1500);
{ const q0 = new URLSearchParams(location.search).get("q"); if (q0) setTimeout(() => { openPalette(); $("paletteInput").value = q0; runPalette(q0); }, 1200); }
$("badgeBtn").onclick = () => $("btnCams").click();
$("btnRestart").onclick = () => { feed.restart(); $("btnPlay").textContent = "PAUSE"; };
$("btnSpeed").onclick = e => { const speeds = [1, 3, 6, 12, 30]; feed.speed = speeds[(speeds.indexOf(feed.speed) + 1) % speeds.length]; e.currentTarget.textContent = `×${feed.speed}`; };
document.addEventListener("keydown", e => {
  // no single-key shortcuts while typing (palette input etc.) or while the palette is open
  if (paletteOpen || ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === " ") { e.preventDefault(); $("btnPlay").click(); } if (e.key === "f") $("btnFreeze").click(); if (e.key === "c") $("btnCenter").click(); });

// live mode: ?ws=ws://gn100-223b:8765
const wsUrl = new URLSearchParams(location.search).get("ws");
if (wsUrl) { map.on("load", () => feed.connectLive(wsUrl)); $("chipLive").classList.add("on"); }
