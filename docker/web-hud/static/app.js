"use strict";
const COLORS = ["#4cc9f0","#f72585","#b5e48c","#ffd166","#c77dff","#ff9f1c","#06d6a0","#ef476f"];
const EWC = {GREEN:"#2ecc71", DEGRADED:"#f1c40f", JAMMED:"#e74c3c", UNKNOWN:"#7f8c8d"};
const TCOL = ["#e63946","#2ecc71","#4dabf7","#ffd166"];
const LEVELS = [[30,"L1"],[50,"L2"],[80,"L3"],[100,"L4"]];
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = (v, d = 1) => (typeof v === "number" && isFinite(v)) ? v.toFixed(d) : "—";
const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };

let cfg = null, N = 5, map = null, ws = null;
const M = {drone: {}, trail: {}, sigma: {}, link: {}, sec: [], pos: {}, taskLayer: null};
const splitSeen = new Set();
const FEED = [], seenN = new Set();

// ───────────── старт ─────────────
async function loadConfig() {
  for (;;) {
    try { const r = await fetch("/api/config"); if (r.ok) { cfg = await r.json(); return; } } catch (e) { /* retry */ }
    setConn("ждёт scene.json", false);
    await new Promise((r) => setTimeout(r, 2000));
  }
}

async function init() {
  await loadConfig();
  N = cfg.swarm_size;
  buildMap(); buildGraph(); buildCtrl(); buildFilter(); buildTasking(); buildEvidence();
  setTimeout(() => map.invalidateSize(), 100);
  connect();
}

function setConn(text, ok) { const e = $("conn"); e.textContent = "HUD " + text; e.className = "b " + (ok ? "GREEN" : "JAMMED"); }

// ───────────── карта (Leaflet CRS.Simple: lat = y(север), lng = x(восток), метры) ─────────────
function buildMap() {
  const sc = cfg.scene, A = cfg.mission.search_area;
  const xs = [A.x_min, A.x_max, ...sc.drones.map((d) => d.x_m)];
  const ys = [A.y_min, A.y_max, ...sc.drones.map((d) => d.y_m)];
  const b = [[Math.min(...ys) - 12, Math.min(...xs) - 12], [Math.max(...ys) + 12, Math.max(...xs) + 12]];
  map = L.map("map", {crs: L.CRS.Simple, minZoom: -4, maxZoom: 5, zoomSnap: 0.25, attributionControl: false});
  map.fitBounds(b);
  const gx0 = Math.floor(b[0][1] / 10) * 10, gx1 = Math.ceil(b[1][1] / 10) * 10;
  const gy0 = Math.floor(b[0][0] / 10) * 10, gy1 = Math.ceil(b[1][0] / 10) * 10;
  const gs = {color: "#222c3a", weight: 1, interactive: false};
  for (let x = gx0; x <= gx1; x += 10) L.polyline([[gy0, x], [gy1, x]], gs).addTo(map);
  for (let y = gy0; y <= gy1; y += 10) L.polyline([[y, gx0], [y, gx1]], gs).addTo(map);

  const {cols, rows} = cfg.mission.grid, cw = (A.x_max - A.x_min) / cols, rh = (A.y_max - A.y_min) / rows;
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const p = r * cols + c, x0 = A.x_min + c * cw, y0 = A.y_min + r * rh;
    M.sec[p] = L.rectangle([[y0, x0], [y0 + rh, x0 + cw]],
      {color: "#5b6b7c", weight: 1, fillColor: "#5b6b7c", fillOpacity: 0.05, interactive: false}).addTo(map);
    L.marker([y0 + rh - 3, x0 + 4], {icon: L.divIcon({className: "lbl", html: "S" + p, iconSize: [30, 14]}), interactive: false}).addTo(map);
  }
  for (const o of sc.obstacles) {
    L.rectangle([[o.y_m - o.size_m[1] / 2, o.x_m - o.size_m[0] / 2], [o.y_m + o.size_m[1] / 2, o.x_m + o.size_m[0] / 2]],
      {color: "#8d99ae", weight: 1, fillColor: "#8d99ae", fillOpacity: 0.5, interactive: false}).addTo(map);
  }
  sc.targets.forEach((t, i) => L.circle([t.y_m, t.x_m], {radius: t.radius_m, color: TCOL[i % 4], weight: 2,
    fillColor: TCOL[i % 4], fillOpacity: 0.35, interactive: false}).addTo(map));

  M.taskLayer = L.layerGroup().addTo(map);
  for (let i = 1; i <= N; i++) {
    const col = COLORS[i - 1], sp = sc.drones.find((d) => d.id === i) || {x_m: 0, y_m: 0};
    const ll = [sp.y_m, sp.x_m];
    M.trail[i] = L.polyline([], {color: col, weight: 2, opacity: 0.6, interactive: false}).addTo(map);
    M.link[i] = L.polyline([], {color: col, weight: 1, dashArray: "2 6", opacity: 0.8, interactive: false}).addTo(map);
    M.sigma[i] = L.circle(ll, {radius: 1, color: col, weight: 1, dashArray: "3 4", fillOpacity: 0, interactive: false}).addTo(map);
    M.drone[i] = L.circleMarker(ll, {radius: 8, color: EWC.UNKNOWN, weight: 3, fillColor: col, fillOpacity: 0.3, opacity: 0.4}).addTo(map);
    M.drone[i].bindTooltip("D" + i, {permanent: true, direction: "right", offset: [10, 0], className: "dlbl"});
  }
}

function refDrone(s) {
  let best = null;
  for (const d of Object.values(s.drones)) {
    if (!d.pkt || d.tele_age === null) continue;
    if (!best || d.tele_age < best.tele_age) best = d;
  }
  return best;
}

function updateMap(s, ref) {
  const doneAll = new Set(), exec = {};
  for (const [k, d] of Object.entries(s.drones)) {
    if (!d.pkt) continue;
    for (const t of d.pkt.swarm.done || []) doneAll.add(t);
    if (d.pkt.swarm.task_name) exec[d.pkt.swarm.task_name] = +k;
  }
  const open = ref ? (ref.pkt.swarm.open || []) : [];
  for (const t of open) { const m = /^S(\d+)\./.exec(t.name); if (m) splitSeen.add(+m[1]); }
  for (const n of Object.keys(exec)) { const m = /^S(\d+)\./.exec(n); if (m) splitSeen.add(+m[1]); }

  for (let p = 0; p < M.sec.length; p++) {
    let st = {color: "#5b6b7c", fillColor: "#5b6b7c", fillOpacity: 0.05, weight: 1, dashArray: splitSeen.has(p) ? "4 3" : null};
    const who = Object.keys(exec).find((n) => n === "S" + p || n.startsWith("S" + p + "."));
    if (doneAll.has(p) && !splitSeen.has(p)) st = {...st, color: "#2ecc71", fillColor: "#2ecc71", fillOpacity: 0.18};
    else if (who !== undefined) { const c = COLORS[exec[who] - 1]; st = {...st, color: c, fillColor: c, fillOpacity: 0.15, weight: 2}; }
    M.sec[p].setStyle(st);
  }

  M.taskLayer.clearLayers();
  for (const t of open) {
    const owner = exec[t.name], col = owner ? COLORS[owner - 1] : (t.kind === "TARGET" ? TCOL[(t.id - 6) % 4] : "#8493a6");
    L.circleMarker([t.cy, t.cx], {radius: t.kind === "TARGET" ? 6 : 4, color: col, weight: 2, fillColor: col, fillOpacity: 0.5, interactive: false})
      .bindTooltip(t.name, {permanent: true, direction: "top", offset: [0, -4], className: "tlbl"}).addTo(M.taskLayer);
  }

  for (let i = 1; i <= N; i++) {
    const d = s.drones[i], p = d && d.pkt;
    if (!p) continue;
    const st = ewOf(s, i).status || "UNKNOWN", [x, y, z] = p.pos, ll = [y, x];
    const stale = d.tele_age === null || d.tele_age > 3, n = p.nav;
    M.drone[i].setLatLng(ll).setStyle({color: EWC[st] || EWC.UNKNOWN, fillOpacity: stale ? 0.3 : 0.9, opacity: stale ? 0.4 : 1});
    M.drone[i].setTooltipContent(esc(`D${i} ${fmt(z)}м ${n.src || "?"}${n.gps_suspect ? " ⚠GPS" : ""}`));
    M.sigma[i].setLatLng(ll).setRadius(Math.max(0.5, n.sigma || 0))
      .setStyle({color: n.gps_suspect ? "#e74c3c" : COLORS[i - 1], weight: n.gps_suspect ? 2 : 1});
    const tr = M.trail[i], pts = tr.getLatLngs(), lp = pts[pts.length - 1];
    if (!lp || Math.hypot(lp.lat - y, lp.lng - x) > 0.3) { pts.push(L.latLng(y, x)); if (pts.length > 200) pts.shift(); tr.setLatLngs(pts); }
    const tk = open.find((o) => o.name === p.swarm.task_name);
    M.link[i].setLatLngs(tk ? [ll, [tk.cy, tk.cx]] : []);
  }
}

// ───────────── граф связности ─────────────
function buildGraph() {
  const cx = 130, cy = 105, R = 78;
  let h = "";
  for (let i = 1; i <= N; i++) { const a = -Math.PI / 2 + 2 * Math.PI * (i - 1) / N; M.pos[i] = [cx + R * Math.cos(a), cy + R * Math.sin(a)]; }
  for (let i = 1; i <= N; i++) for (let j = i + 1; j <= N; j++) {
    const [x1, y1] = M.pos[i], [x2, y2] = M.pos[j];
    h += `<line id="e${i}-${j}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#333" stroke-width="1"><title></title></line>`;
  }
  for (let i = 1; i <= N; i++) {
    const [x, y] = M.pos[i];
    h += `<g id="n${i}"><circle cx="${x}" cy="${y}" r="17" fill="${COLORS[i - 1]}" fill-opacity="0.25" stroke="#7f8c8d" stroke-width="4"/>` +
         `<text x="${x}" y="${y + 4}">D${i}</text><text class="sub" x="${x}" y="${y + 29}">q —</text></g>`;
  }
  $("graph").innerHTML = h;
}

function updateGraph(s) {
  const heard = (i) => { const d = s.drones[i]; return d && d.pkt && d.tele_age !== null && d.tele_age < 3 ? new Set(d.pkt.mesh.heard) : null; };
  const q = (i, j) => { const d = s.drones[i]; const p = d && d.pkt && d.pkt.swarm.peers[String(j)]; return p ? p.q : 0; };
  for (let i = 1; i <= N; i++) for (let j = i + 1; j <= N; j++) {
    const hi = heard(i), hj = heard(j), a = hi ? hi.has(j) : null, b = hj ? hj.has(i) : null;
    const e = $(`e${i}-${j}`);
    let col = "#333", w = 1, dash = "3 3", tip = "нет телеметрии";
    if (a === null && b === null) { /* значения по умолчанию */ }
    else if (a && b) { col = "#2ecc71"; w = 1 + 3 * (q(i, j) + q(j, i)) / 2; dash = ""; tip = `двусторонняя связь (q ${fmt(q(i, j), 2)} / ${fmt(q(j, i), 2)})`; }
    else if (a || b) { col = "#f39c12"; w = 2; dash = "6 4"; tip = a ? `D${i} слышит D${j}, обратно нет` : `D${j} слышит D${i}, обратно нет`; }
    else { col = "#c0392b"; w = 1; dash = "2 4"; tip = "связи нет"; }
    e.setAttribute("stroke", col); e.setAttribute("stroke-width", w.toFixed(1)); e.setAttribute("stroke-dasharray", dash);
    e.firstChild.textContent = `D${i}–D${j}: ${tip}`;
  }
  for (let i = 1; i <= N; i++) {
    const d = s.drones[i], g = $("n" + i); if (!g) continue;
    const st = ewOf(s, i).status || "UNKNOWN", stale = !d || !d.pkt || d.tele_age === null || d.tele_age > 3;
    g.querySelector("circle").setAttribute("stroke", EWC[st] || EWC.UNKNOWN);
    g.setAttribute("opacity", stale ? 0.4 : 1);
    g.querySelector(".sub").textContent = d && d.pkt ? "q " + fmt(d.pkt.mesh.link_q, 2) : "q —";
  }
}

// ───────────── карточки бортов ─────────────
const ewOf = (s, i) => (s.ew && s.ew.drones && s.ew.drones[i]) || {};
const badge = (t, c) => `<span class="b ${c}">${esc(t)}</span>`;
const row = (k, v) => `<div class="row"><span>${k}</span><b>${v}</b></div>`;

function meshLabel(m) {
  if (!m || !m.active) return "норма";
  const p = m.profile || {}, lv = LEVELS.find((l) => l[0] === Math.round(p.loss_pct));
  return `${lv ? lv[1] : "custom"} · loss ${p.loss_pct}% · ${p.delay_ms}±${p.jitter_ms} мс · ${p.rate_kbit ?? "∞"} кбит/с`;
}

function cardHtml(i, d, ew) {
  const st = ew.status || "UNKNOWN", p = d && d.pkt, c2 = d ? d.c2 : {state: "NO_LINK"};
  let h = `<div class="hd"><i class="sw" style="background:${COLORS[i - 1]}"></i><b>D${i}</b>${badge(st, st)}` +
    badge(c2.state === "OK" ? "C2 OK" : (c2.state === "LOST" ? "C2 LINK LOST" : "C2 —"), c2.state === "OK" ? "GREEN" : (c2.state === "LOST" ? "JAMMED" : "UNKNOWN"));
  if (!p || d.tele_age === null || d.tele_age > 3) h += badge("NO TELEMETRY", "UNKNOWN");
  h += "</div>";
  if (!p) return h;
  const n = p.nav, m = p.mesh, sw = p.swarm, l = p.llm || {}, ls = l.stats || {}, g = ew.gps || {}, gb = ew.gps_bridge || {};
  let gps = `fix ${n.gps_fix}`;
  if (g.active) gps += ` · ${g.mode}`;
  if (gb.mode && gb.mode !== "pass") gps += ` · ${gb.mode} Δ${fmt(gb.bias_north_m, 0)}N/${fmt(gb.bias_east_m, 0)}E`;
  if (n.gps_suspect) gps += " · ⚠ SUSPECT";
  h += row("GPS", esc(gps));
  h += row("NAV", esc(`${n.state} · ${n.src} · σ ${fmt(n.sigma)} м · alt ${fmt(p.pos[2])} м`));
  h += row("Фаза/S1", esc(`${p.phase || "—"} · ${(p.s1 || {}).mode || "—"} · p95 ${(p.s1 || {}).p95_ms ?? "—"} мс`));
  h += row("Препятствие", n.obst_min < 50 ? esc(fmt(n.obst_min) + " м") : "—");
  h += row("Задача", esc(`${sw.task_name || "—"} · эпоха ${sw.epoch} · готово ${sw.done.length}/${sw.total_tasks}`));
  h += row("Mesh", esc(`q ${fmt(m.link_q, 2)} · peers ${m.zenoh_peers} · ${meshLabel(ew.mesh)}`));
  h += row("LLM", esc(`${ls.calls || 0} выз. (✔${ls.accepted || 0} ✘${ls.rejected || 0} ⚠${ls.failed || 0}) · ${l.last_latency_ms ?? "—"} мс`));
  return h;
}

function updateCards(s) {
  const box = $("cards");
  if (!box.childElementCount) for (let i = 1; i <= N; i++) { const c = mk("div", "card"); c.id = "card" + i; box.append(c); }
  for (let i = 1; i <= N; i++) $("card" + i).innerHTML = cardHtml(i, s.drones[i], ewOf(s, i));
}

// ───────────── верхняя панель ─────────────
function updateTop(s, ref) {
  const ew = s.ew, ok = s.ew_ok, st = ok && ew ? ew.overall : "UNKNOWN", e = $("ewb");
  e.textContent = ok ? "EW: " + st : "EW-CONTROLLER OFFLINE";
  e.className = "b " + (EWC[st] ? st : "UNKNOWN");
  $("top").className = ok && st === "JAMMED" ? "jam" : "";
  const sc = ok && ew ? ew.scenario : null;
  $("scn-st").textContent = sc && sc.running ? `сценарий ${sc.name} · ${fmt(sc.elapsed_s, 0)} с` : "";
  if (ref) {
    const sw = ref.pkt.swarm;
    $("kpi").textContent = `эпоха ${sw.epoch} · задач готово ${sw.done.length}/${sw.total_tasks} · живых бортов ${sw.alive.length}`;
  } else $("kpi").textContent = "нет телеметрии";
  $("conv").textContent = convText(s.converge);
}

// Время слияния считает HUD (convergence.py); дубликаты во время разделения и после слияния показываются отдельно.
function convText(c) {
  if (!c || c.phase === "idle") return "";
  const dup = `дубли: во время разделения ${c.dup_split.length} · после слияния ${c.dup_merge.length}`;
  if (c.phase === "split") return (c.split_effective ? "РАЗДЕЛЕНИЕ применено" : "РАЗДЕЛЕНИЕ: сеть ещё не разошлась") + ` · ${dup}`;
  if (c.phase === "merging")
    return `слияние… ${fmt(c.elapsed_s, 1)} с · эпох ${c.epochs_merge.length}` + (c.waiting_for.length ? ` · ждём D${c.waiting_for.join(",D")}` : "") + ` · ${dup}`;
  return `СЛИЯНИЕ ${fmt(c.merge_s, 2)} с · эпох ${c.epochs_merge.length} · ${dup}`;
}

function render(s) {
  const ref = refDrone(s);
  updateTop(s, ref); updateMap(s, ref); updateGraph(s); updateCards(s);
}

// ───────────── консоль ─────────────
let fDrone = "all", fEv = true;
const vis = (f) => (fDrone === "all" || +fDrone === f.drone) && (f.kind !== "event" || fEv);

function feedEl(f) {
  const e = mk("div", "fi " + f.kind);
  const d = mk("span", "d", "D" + f.drone); d.style.color = COLORS[(f.drone - 1) % COLORS.length];
  e.append(mk("span", "t", `[${fmt(f.t, 0)}s]`), d);
  if (f.kind === "llm") {
    const lat = f.latency_ms != null ? ` ${(f.latency_ms / 1000).toFixed(1)}с` : "";
    e.append(mk("span", "src " + (f.source || "S2"), f.source || "S2"),
             mk("span", "tool", f.tool || "—"), mk("span", "args", f.args ? JSON.stringify(f.args) : ""),
             mk("span", "v " + f.verdict, (f.verdict || "?") + lat));
    if (f.reasoning) e.append(mk("div", "why", "« " + f.reasoning + " »"));
    if (f.reason) e.append(mk("div", "why err", "⤷ " + f.reason));
  } else e.append(mk("span", "ev", "• " + f.text));
  return e;
}

function addFeed(items) {
  const box = $("console");
  for (const f of items) {
    if (seenN.has(f.n)) continue;
    seenN.add(f.n); FEED.push(f);
    if (vis(f)) box.append(feedEl(f));
  }
  while (FEED.length > 600) FEED.shift();
  while (box.childElementCount > 600) box.firstChild.remove();
  if ($("autoscroll").checked) box.scrollTop = box.scrollHeight;
}

function rerender() { $("console").replaceChildren(...FEED.filter(vis).map(feedEl)); }

function buildFilter() {
  const s = $("fdrone");
  s.append(new Option("все борты", "all"));
  for (let i = 1; i <= N; i++) s.append(new Option("D" + i, String(i)));
  s.onchange = () => { fDrone = s.value; rerender(); };
  $("showEv").onchange = (e) => { fEv = e.target.checked; rerender(); };
}

// ───────────── управление РЭБ ─────────────
const num = (id) => { const v = parseFloat($(id).value); return isFinite(v) ? v : 0; };
const dur = () => { const v = parseFloat($("dur").value); return v > 0 ? v : null; };
function selDrones() {
  const ids = [...document.querySelectorAll("#dsel input:checked")].map((e) => +e.value);
  return ids.length === 0 || ids.length === N ? null : ids;
}
const countBad = (o) => (o && typeof o === "object") ? (o.ok === false ? 1 : 0) + Object.values(o).reduce((a, v) => a + countBad(v), 0) : 0;

const ew = (name, body) => hudPost("/api/ew/" + name, body);

async function hudPost(path, body) {
  const m = $("ewmsg"); m.textContent = "…";
  try {
    const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json", "X-HUD": "1"}, body: JSON.stringify(body || {})});
    const data = await r.json().catch(() => ({}));
    if (!r.ok) m.textContent = `ошибка ${r.status}: ${String(data.detail || data.error || "").slice(0, 120)}`;
    else { const bad = countBad(data); m.textContent = bad ? `выполнено, ошибок у агентов: ${bad}` : "OK"; }
  } catch (e) { m.textContent = "сеть: " + e; }
}

async function loadScenarios() {
  try {
    const r = await fetch("/api/ew/scenarios"); if (!r.ok) return;
    const list = await r.json(), s = $("scn"); s.replaceChildren();
    for (const x of list) s.append(new Option(`${x.name} (${x.steps})`, x.name));
  } catch (e) { /* ew-controller ещё не поднят */ }
}

function buildCtrl() {
  const ds = $("dsel");
  for (let i = 1; i <= N; i++) {
    const l = mk("label"); const c = mk("input"); c.type = "checkbox"; c.value = String(i); c.checked = true;
    l.append(c, document.createTextNode(" D" + i + "  ")); ds.append(l);
  }
  $("c2cut").onclick = () => ew("c2", {drones: selDrones(), duration_s: dur(), include_onboard: $("onb").checked});
  $("c2res").onclick = () => ew("restore", {channel: "c2", drones: selDrones()});
  $("gps").onclick = () => ew("gps", {mode: $("gmode").value, drones: selDrones(), duration_s: dur(),
    rate_mps: num("rate"), heading_deg: num("head"), target_north_m: num("tn"), target_east_m: num("te")});
  $("gpsres").onclick = () => ew("restore", {channel: "gps", drones: selDrones()});
  document.querySelectorAll("button[data-lvl]").forEach((b) => b.onclick = () => ew("mesh", {level: b.dataset.lvl, drones: selDrones(), duration_s: dur()}));
  $("meshres").onclick = () => ew("restore", {channel: "mesh", drones: selDrones()});
  $("split").onclick = () => hudPost("/api/split", {});
  $("heal").onclick = () => hudPost("/api/heal", {});
  $("reset").onclick = () => ew("restore", {channel: "all", drones: null});
  $("scnrun").onclick = () => $("scn").value && ew("scenario_run", {name: $("scn").value});
  $("scnstop").onclick = () => ew("scenario_stop", {restore: true});
  loadScenarios(); setInterval(loadScenarios, 15000);
}

// ───────────── Commander Tasking & BDA Evidence ─────────────
function buildTasking() {
  const btn = $("cmd-send");
  const inp = $("cmd-input");
  const st = $("cmd-status");
  const card = $("manifest-info");
  if (!btn || !inp) return;

  btn.onclick = async () => {
    const prompt = inp.value.trim();
    if (!prompt) return;
    st.textContent = "Компиляция Qwen-0.5B (5 бортов)...";
    st.className = "cmd-badge compiling";
    btn.disabled = true;

    try {
      const res = await fetch("/api/tasking", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-HUD": "1"},
        body: JSON.stringify({prompt})
      });
      const data = await res.json();
      btn.disabled = false;
      if (!res.ok) {
        st.textContent = "Ошибка: " + (data.detail || "502");
        st.className = "cmd-badge";
        return;
      }
      const okCount = (data.results || []).filter(r => r.ok).length;
      st.textContent = `Манифест согласован (${okCount}/${N} бортов)`;
      st.className = "cmd-badge ready";

      const firstOk = (data.results || []).find(r => r.ok && r.body);
      if (firstOk && firstOk.body) {
        const b = firstOk.body;
        card.style.display = "block";
        card.innerHTML = `<b>Скомпилированный Task Manifest:</b> Зоны: <code>[${(b.zones_named || []).join(", ")}]</code> (сектора ${(b.active_sectors || []).join(",")}) | ` +
                         `При контакте: <code>${b.on_target}</code> (${b.dwell_s}с) | RTB: <code>${b.rtb_on_complete}</code> | ` +
                         `Инференс: <code>${b.latency_ms} мс</code>`;
      }
    } catch (e) {
      btn.disabled = false;
      st.textContent = "Ошибка сети: " + e.message;
      st.className = "cmd-badge";
    }
  };
}

let lastEvidenceIds = new Set();
async function pollEvidence() {
  try {
    const res = await fetch("/api/evidence");
    if (!res.ok) return;
    const cards = await res.json();
    const badge = $("ev-badge");
    if (badge) badge.textContent = String(cards.length);
    if (!cards.length) return;

    const gal = $("evidence-gallery");
    if (!gal) return;
    const currentIds = new Set(cards.map(c => c.id));
    if (currentIds.size !== lastEvidenceIds.size) {
      lastEvidenceIds = currentIds;
      gal.replaceChildren();
      for (const c of cards.slice().reverse()) {
        const d = mk("div", "ev-card");
        const img = mk("img", "ev-thumb");
        img.src = `/api/evidence/${c.id}/image`;
        img.alt = `Target T${c.target_id}`;

        const body = mk("div", "ev-body");
        const head = mk("div", "ev-head", `Цель T${c.target_id} (Борт D${c.drone})`);
        const pos = mk("div", null, `ENU: (${c.local_enu_m.x}, ${c.local_enu_m.y}, h=${c.local_enu_m.z}м) | Дистанция: ${c.dist_to_target_m}м`);
        const geo = mk("div", "dim", `Координаты: ${c.approx_latlon.lat}°, ${c.approx_latlon.lon}°`);
        const src = mk("div", "dim", `Источник: ${c.nav_source || "VIO/DR"} (GPS fix: ${c.gps_fix || "denied"}) | Фото: ${c.image_bytes}B`);
        const note = mk("div", "ev-note", `* ${c.approx_latlon.note}`);

        body.append(head, pos, geo, src, note);
        d.append(img, body);
        gal.append(d);
      }
    }
  } catch (e) { /* ignore */ }
}

function buildEvidence() {
  const tCon = $("tab-console");
  const tEv = $("tab-evidence");
  const cView = $("console");
  const cTools = $("console-tools");
  const eView = $("evidence-view");
  if (!tCon || !tEv) return;

  tCon.onclick = () => {
    tCon.classList.add("active");
    tEv.classList.remove("active");
    if (cView) cView.style.display = "block";
    if (cTools) cTools.style.display = "flex";
    if (eView) eView.style.display = "none";
  };

  tEv.onclick = () => {
    tEv.classList.add("active");
    tCon.classList.remove("active");
    if (cView) cView.style.display = "none";
    if (cTools) cTools.style.display = "none";
    if (eView) eView.style.display = "block";
    pollEvidence();
  };

  setInterval(pollEvidence, 2000);
}

// ───────────── WebSocket ─────────────
function connect() {
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onopen = () => { setConn("online", true); FEED.length = 0; seenN.clear(); $("console").replaceChildren(); };
  ws.onclose = () => { setConn("offline", false); setTimeout(connect, 1500); };
  ws.onmessage = (e) => { const m = JSON.parse(e.data); render(m.state); if (m.feed.length) addFeed(m.feed); };
}

init();
