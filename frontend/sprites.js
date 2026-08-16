// Original pixel iconography: a kick scooter glyph (no rider) inside a pixel badge.
// Rendered to ImageData at runtime so MapLibre gets one sprite per state — no per-marker SVG.

const SCOOTER = [
  "................",
  "...........##...",
  "..........###...",
  "..........#.....",
  "..........#.....",
  "..........#.....",
  "..........#.....",
  "..........#.....",
  "..........#.....",
  "....######.#....",
  "...##.....###...",
  "..#..#...#...#..",
  "..#..#...#...#..",
  "...##.....###...",
  "................",
  "................",
];
const CAM = [
  "........",
  ".######.",
  ".#....#.",
  ".#.##.#.",
  ".#.##.#.",
  ".#....#.",
  ".######.",
  "...##...",
];
const CAM_DEAD = [
  "........",
  ".#....#.",
  "..#..#..",
  "...##...",
  "...##...",
  "..#..#..",
  ".#....#.",
  "........",
];

export const STATE_COLORS = {
  detected:   { fill:"#8A97AD", ink:"#1B2230", ring:"#3B4658" },
  unverified: { fill:"#F0645A", ink:"#3A0E0A", ring:"#8E2C24" },
  confirmed:  { fill:"#48D06A", ink:"#062810", ring:"#1F7A34" },
  linked:     { fill:"#F6B53A", ink:"#3A2600", ring:"#9C6A0C" },
  lost:       { fill:"#5A6578", ink:"#0F141C", ring:"#2A3140" },
};

function pixelCircle(ctx, cx, cy, r, s, color) {
  ctx.fillStyle = color;
  for (let y = -r; y <= r; y++) for (let x = -r; x <= r; x++)
    if (x*x + y*y <= r*r + r*0.6) ctx.fillRect((cx+x)*s, (cy+y)*s, s, s);
}
function glyph(ctx, rows, ox, oy, s, color) {
  ctx.fillStyle = color;
  rows.forEach((row, y) => [...row].forEach((c, x) => { if (c === "#") ctx.fillRect((ox+x)*s, (oy+y)*s, s, s); }));
}

/** Badge sprite: 24x24 logical px at scale s. */
export function badge(state, s = 3, big = false) {
  const N = big ? 32 : 24, r = big ? 14 : 10, c = STATE_COLORS[state] || STATE_COLORS.detected;
  const cv = document.createElement("canvas"); cv.width = cv.height = N * s;
  const ctx = cv.getContext("2d");
  pixelCircle(ctx, N/2, N/2, r,   s, c.ring);
  pixelCircle(ctx, N/2, N/2, r-2, s, c.fill);
  glyph(ctx, SCOOTER, N/2 - 8, N/2 - 8, s, c.ink);
  return { data: ctx.getImageData(0, 0, N*s, N*s), pixelRatio: s };
}

/** Cluster sprite: cream ring badge; count text is drawn by MapLibre. */
export function cluster(s = 3) {
  const N = 36; const cv = document.createElement("canvas"); cv.width = cv.height = N * s;
  const ctx = cv.getContext("2d");
  pixelCircle(ctx, N/2, N/2, 16, s, "#143B6E");
  pixelCircle(ctx, N/2, N/2, 14, s, "#F5E6C4");
  pixelCircle(ctx, N/2, N/2, 11, s, "#F0645A");
  return { data: ctx.getImageData(0, 0, N*s, N*s), pixelRatio: s };
}

export function camIcon(alive, kind = "sdot", s = 3) {
  // 16x16 logical px: dark outline, bright body, glyph. Bigger + brighter than before so 600+ cams read on the navy map.
  const N = 16; const cv = document.createElement("canvas"); cv.width = cv.height = N * s;
  const ctx = cv.getContext("2d");
  const wsdot = kind === "wsdot";
  const body = !alive ? "#F0645A" : wsdot ? "#F5E6C4" : "#6FB6F0";
  const ink  = !alive ? "#3A0E0A" : wsdot ? "#3A2600" : "#0B2247";
  ctx.fillStyle = "#0B1220"; ctx.fillRect(0, 0, N*s, N*s);                 // outline
  ctx.fillStyle = body; ctx.fillRect(2*s, 2*s, (N-4)*s, (N-4)*s);           // body
  ctx.fillStyle = "rgba(255,255,255,.35)"; ctx.fillRect(2*s, 2*s, (N-4)*s, s); // top highlight
  glyph(ctx, alive ? CAM : CAM_DEAD, 4, 4, s, ink);
  return { data: ctx.getImageData(0, 0, N*s, N*s), pixelRatio: s };
}

/** Frame size a sighting's bbox is expressed in: explicit `frame_size`, else inferred
 *  (hand-written fake data used 640x360; the real detector emits 1280x720 clip frames). */
export function bboxFrame(s) {
  if (s?.frame_size) return s.frame_size;
  const [x, y, w, h] = s?.bbox || [0, 0, 0, 0];
  return (x + w <= 640 && y + h <= 360) ? [640, 360] : [1280, 720];
}

/** Overlay only: draw the detection box + label over a live still/video. */
export function drawDetectionOverlay(canvas, sighting) {
  const ctx = canvas.getContext("2d"), W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  if (!sighting?.bbox) return;
  const [fw, fh] = bboxFrame(sighting);
  const [bx, by, bw, bh] = sighting.bbox;
  const x = bx * W / fw, y = by * H / fh, w = bw * W / fw, h = bh * H / fh;
  const c = STATE_COLORS[sighting.state]?.fill || "#fff";
  ctx.strokeStyle = c; ctx.lineWidth = 2; ctx.strokeRect(x - 2, y - 2, w + 4, h + 4);
  ctx.fillStyle = c; ctx.fillRect(x - 2, y - 14, 78, 12);
  ctx.fillStyle = "#000"; ctx.font = "9px monospace"; ctx.fillText(`${sighting.class} ${sighting.conf.toFixed(2)}`, x + 1, y - 5);
}

/** Draw the fake "raw camera" tile: a road, lane lines, the detection box. Used when the camera has no image feed. */
export function drawCameraFrame(canvas, sighting, camera, tick) {
  const ctx = canvas.getContext("2d"), W = canvas.width, H = canvas.height;
  ctx.imageSmoothingEnabled = false;
  // sky / buildings / road
  ctx.fillStyle = "#1a1f2a"; ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = "#262c3a"; for (let i = 0; i < 6; i++) ctx.fillRect(i*44, 30 - (i%3)*8, 40, 60);
  ctx.fillStyle = "#3a3f47"; ctx.beginPath(); ctx.moveTo(W*0.42, 70); ctx.lineTo(W*0.58, 70); ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath(); ctx.fill();
  ctx.fillStyle = "#2f8f5a"; ctx.beginPath(); ctx.moveTo(W*0.44, 70); ctx.lineTo(W*0.48, 70); ctx.lineTo(W*0.28, H); ctx.lineTo(0, H); ctx.closePath(); ctx.fill(); // bike lane, green paint
  ctx.strokeStyle = "#cfcfcf"; ctx.setLineDash([8, 10]); ctx.lineDashOffset = -(tick*2 % 18); ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(W*0.5, 70); ctx.lineTo(W*0.62, H); ctx.stroke(); ctx.setLineDash([]);
  // grain
  for (let i = 0; i < 120; i++) { ctx.fillStyle = `rgba(255,255,255,${Math.random()*0.05})`; ctx.fillRect(Math.random()*W, Math.random()*H, 2, 2); }
  // overlay text
  ctx.fillStyle = "#e8e8e8"; ctx.font = "10px monospace";
  ctx.fillText((camera?.name || "").toUpperCase(), 6, 12);
  ctx.fillText(new Date(sighting?.t || Date.now()).toLocaleTimeString(), W - 66, H - 6);
  if (!sighting) return;
  // detection: bbox given in a 640x360 source frame → scale
  const [bx, by, bw, bh] = sighting.bbox || [300, 180, 36, 70];
  const sx = W/640, sy = H/360, x = bx*sx, y = by*sy, w = bw*sx, h = bh*sy;
  // hi-vis rider blob
  ctx.fillStyle = sighting.track_id === "T-SUBJ" ? "#ff7a1a" : "#c9c9c9"; ctx.fillRect(x+w*0.3, y+h*0.15, w*0.4, h*0.45);
  ctx.fillStyle = "#222"; ctx.fillRect(x+w*0.2, y+h*0.6, w*0.6, h*0.4);
  const c = STATE_COLORS[sighting.state]?.fill || "#fff";
  ctx.strokeStyle = c; ctx.lineWidth = 2; ctx.strokeRect(x-2, y-2, w+4, h+4);
  ctx.fillStyle = c; ctx.fillRect(x-2, y-14, 78, 12);
  ctx.fillStyle = "#000"; ctx.font = "9px monospace"; ctx.fillText(`${sighting.class} ${sighting.conf.toFixed(2)}`, x+1, y-5);
}

// ---- Brand: original pixel badge — hi-vis scooter helmet with a cream visor. Not a mask. ----
// . transparent  # outline  o orange  v visor cream  h highlight  g green LED
const LOGO = [
  ".......######.......",
  ".....##oooooo##.....",
  "....#oohoooooo#.....",
  "...#oohooooooooo#...",
  "..#ooooooooooooooo#.",
  "..#oo############o#.",
  "#oo#vvvvvvvvvvvv#o#.",
  "#o#vvvvvvvvvvvvvv#o#",
  "#o#vvvvvvvvvvvvvv#o#",
  "#o#vvvvvvvvvvvvvv#o#",
  "#oo#vvvvvvvvvvvv#o#.",
  "..#oo############o#.",
  "..#ooooooooooooooo#.",
  "...#ooooooooooooo#..",
  "...#oooooooooogo#...",
  "....#ooooooooooo#...",
  ".....##ooooooo##....",
  ".......#######......",
];
const LOGO_COLORS = { "#": "#1B1410", "o": "#F26B2B", "v": "#F5E6C4", "h": "#FFA06A", "g": "#48D06A" };
export function drawLogo(canvas, s = 4) {
  const W = LOGO[0].length, H = LOGO.length; canvas.width = W * s; canvas.height = H * s;
  const ctx = canvas.getContext("2d");
  LOGO.forEach((row, y) => [...row].forEach((c, x) => { if (LOGO_COLORS[c]) { ctx.fillStyle = LOGO_COLORS[c]; ctx.fillRect(x*s, y*s, s, s); } }));
  return canvas;
}

// ---- more pixel art (original): spider silhouette badge, rider avatar, hearts ----
const SPIDER = [
  "....#..#....",
  "..#..##..#..",
  "...#.##.#...",
  ".##.####.##.",
  "#..######..#",
  "..########..",
  "..########..",
  "#..######..#",
  ".##.####.##.",
  "...#.##.#...",
  "..#..##..#..",
  "....#..#....",
];
const RIDER = [   // 12x18: helmeted rider standing on a kick scooter
  "....####....",
  "...######...",
  "...#vvvv#...",
  "...######...",
  "....o..o....",
  "..oooooooo..",
  ".o.oooooo.o.",
  ".o.oooooo.o.",
  "...oooooo...",
  "...##..##...",
  "...##..##...",
  "...##..##..#",
  "..###..###.#",
  "...........#",
  "..#########.",
  ".#.........#",
  "#.#.......#.#",
  ".#.........#.",
];
const HEART = [
  ".##..##.",
  "########",
  "########",
  ".######.",
  "..####..",
  "...##...",
];
const RIDER_COLORS = { "#": "#1B1410", "v": "#F5E6C4", "o": "#F26B2B" };

export function drawGlyph(canvas, rows, colors, s = 4, bg = null) {
  const W = Math.max(...rows.map(r => r.length)), H = rows.length; canvas.width = W * s; canvas.height = H * s;
  const ctx = canvas.getContext("2d");
  if (bg) { ctx.fillStyle = bg; ctx.fillRect(0, 0, W * s, H * s); }
  rows.forEach((row, y) => [...row].forEach((c, x) => { const col = typeof colors === "string" ? (c === "#" ? colors : null) : colors[c]; if (col) { ctx.fillStyle = col; ctx.fillRect(x * s, y * s, s, s); } }));
  return canvas;
}
export const GLYPHS = { SPIDER, RIDER, HEART, RIDER_COLORS };

/** Web-radar minimap: concentric rings + spokes, sightings as dots relative to the centre. */
export function drawRadar(canvas, centre, points, radiusM = 900, sweep = 0) {
  const ctx = canvas.getContext("2d"), W = canvas.width, H = canvas.height, R = W / 2 - 4, cx = W / 2, cy = H / 2;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#061024"; ctx.beginPath(); ctx.arc(cx, cy, R + 2, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = "#2E5FAE"; ctx.lineWidth = 1;
  for (let i = 1; i <= 4; i++) { ctx.beginPath(); ctx.arc(cx, cy, R * i / 4, 0, Math.PI * 2); ctx.stroke(); }
  for (let a = 0; a < 12; a++) { const t = a * Math.PI / 6; ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + R * Math.cos(t), cy + R * Math.sin(t)); ctx.stroke(); }
  // sweep
  const g = ctx.createConicGradient ? ctx.createConicGradient(sweep, cx, cy) : null;
  if (g) { g.addColorStop(0, "rgba(72,208,106,.35)"); g.addColorStop(0.15, "rgba(72,208,106,0)"); g.addColorStop(1, "rgba(72,208,106,0)");
    ctx.fillStyle = g; ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill(); }
  if (!centre) return;
  const mPerDegLat = 110540, mPerDegLon = 111320 * Math.cos(centre.lat * Math.PI / 180);
  for (const p of points) {
    const dx = (p.lon - centre.lon) * mPerDegLon, dy = (p.lat - centre.lat) * mPerDegLat;
    const d = Math.hypot(dx, dy); if (d > radiusM) continue;
    const x = cx + dx / radiusM * R, y = cy - dy / radiusM * R;
    ctx.fillStyle = p.color || "#F0645A"; ctx.fillRect(Math.round(x) - 2, Math.round(y) - 2, 4, 4);
  }
  ctx.fillStyle = "#F6B53A"; ctx.fillRect(cx - 3, cy - 3, 6, 6);
  ctx.strokeStyle = "#F6B53A"; ctx.strokeRect(cx - 6.5, cy - 6.5, 13, 13);
}


// police shield badge for dispatch positions
const SHIELD = [
  ".########.",
  "#vvvvvvvv#",
  "#v#vvvv#v#",
  "#vvvvvvvv#",
  "#vv####vv#",
  "#vvvvvvvv#",
  ".#vvvvvv#.",
  "..#vvvv#..",
  "...#vv#...",
  "....##....",
];
export function shieldIcon(s = 3) {
  const N = 14; const cv = document.createElement("canvas"); cv.width = cv.height = N * s;
  const ctx = cv.getContext("2d");
  glyph(ctx, SHIELD.map(r => r.replace(/v/g, "v")), 2, 2, s, "#0B2247");
  SHIELD.forEach((row, y) => [...row].forEach((c, x) => { if (c === "v") { ctx.fillStyle = "#4E8FD1"; ctx.fillRect((2 + x) * s, (2 + y) * s, s, s); } }));
  return { data: ctx.getImageData(0, 0, N * s, N * s), pixelRatio: s };
}
