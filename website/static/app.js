/* Kartenbot Dashboard — Frontend-Logik (Vanilla JS + Chart.js) */
"use strict";

const state = {
  range: localStorage.getItem("kb_range") || "7d",
  tab: localStorage.getItem("kb_tab") || "health",
  charts: {},
  autoRefresh: localStorage.getItem("kb_auto") !== "0",
  timer: null,
  cache: {},                      // letzte API-Antworten je Tab (für Re-Render ohne Fetch)
  showDone: {},                   // Toggle-Zustand "abgeschlossene anzeigen" je Liste
  detailUser: null,               // aktuell analysierter User im Ultra-Detail-Tab
  loading: false,
};

const TAB_TITLES = {
  health: "Health & Logs",
  players: "Spieler & Economy",
  battles: "Battles & Missionen",
  analytics: "Commands & Invites",
  detail: "Spieler-Analyse",
  admin: "Admin",
};

const ACTIVE_STATUSES = new Set(["active", "running", "pending"]);

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => Number(n ?? 0).toLocaleString("de-DE");

function fmtBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0, v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtDuration(seconds) {
  if (seconds == null || seconds < 0) return "–";
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function fmtEpoch(epoch) {
  if (!epoch) return "–";
  return new Date(epoch * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
}

/* Relative Zeit ("vor 5 m") — absolute Zeit steckt im title-Attribut */
function fmtAgo(epoch) {
  if (!epoch) return "–";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (s < 60) return "gerade eben";
  if (s < 3600) return `vor ${Math.floor(s / 60)} m`;
  if (s < 86400) return `vor ${Math.floor(s / 3600)} h`;
  return `vor ${Math.floor(s / 86400)} d`;
}
const agoSpan = (epoch) => `<span title="${fmtEpoch(epoch)}">${fmtAgo(epoch)}</span>`;

function statusBadge(status) {
  const s = String(status || "?").toLowerCase();
  let cls = "st-done";
  if (ACTIVE_STATUSES.has(s)) cls = "st-active";
  else if (["failed", "cancelled", "canceled", "deleted", "error", "declined"].includes(s)) cls = "st-bad";
  else if (["paused", "waiting"].includes(s)) cls = "st-warn";
  return `<span class="st ${cls}">${esc(s)}</span>`;
}

let _lastToast = { msg: "", at: 0 };
function toast(message, isError = false) {
  // Gleiche Meldung nicht alle 15 s erneut anzeigen (Auto-Refresh-Fehler-Spam)
  const now = Date.now();
  if (message === _lastToast.msg && now - _lastToast.at < 12000) return;
  _lastToast = { msg: message, at: now };
  const el = $("toast");
  el.textContent = message;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.className = "toast"), 3800);
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let data = null;
  try { data = await res.json(); } catch { /* leere Antwort */ }
  if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
  return data;
}

/* Setzt innerHTML nur bei Änderung — verhindert Flackern, Scroll-Reset und
   unnötige Re-Renders beim Auto-Refresh. Gibt true zurück, wenn neu gerendert. */
function setHTML(id, html) {
  const el = typeof id === "string" ? $(id) : id;
  if (!el) return false;
  if (el._html === html) return false;
  el._html = html;
  el.innerHTML = html;
  return true;
}

function confirmAction(text) {
  return new Promise((resolve) => {
    $("confirmText").textContent = text;
    $("confirmModal").classList.add("show");
    const done = (answer) => {
      $("confirmModal").classList.remove("show");
      $("confirmYes").onclick = $("confirmNo").onclick = null;
      resolve(answer);
    };
    $("confirmYes").onclick = () => done(true);
    $("confirmNo").onclick = () => done(false);
  });
}

/* ============================ Namen (User & Gilden) ============================ */

const NAMES = { users: {}, guilds: {} };
let namesInFlight = false;

function userLabel(id) {
  const sid = String(id ?? "");
  if (!sid || sid === "0") return '<span class="muted">System</span>';
  const n = NAMES.users[sid];
  return `<span data-uid="${esc(sid)}" title="${esc(sid)}">${esc(n || sid)}</span>`;
}

function guildLabel(id, showId = false) {
  const sid = String(id ?? "");
  if (!sid || sid === "0") return "";
  const n = NAMES.guilds[sid];
  return `<span data-gid="${esc(sid)}"${showId ? ' data-showid="1"' : ""} title="${esc(sid)}">${esc(n || sid)}</span>`;
}

function applyNames() {
  document.querySelectorAll("[data-uid]").forEach((el) => {
    const n = NAMES.users[el.dataset.uid];
    if (n && el.textContent !== n) el.textContent = n;
  });
  document.querySelectorAll("[data-gid]").forEach((el) => {
    const n = NAMES.guilds[el.dataset.gid];
    if (!n) return;
    if (el.dataset.showid) {
      const want = `${esc(n)} <span class="muted mono">${esc(el.dataset.gid)}</span>`;
      if (el._nameHtml !== want) { el._nameHtml = want; el.innerHTML = want; }
    } else if (el.textContent !== n) {
      el.textContent = n;
    }
  });
  updateKnownNames();
}

/* Autocomplete-Datalist mit allen bisher bekannten User-Namen füllen */
function updateKnownNames() {
  const el = $("knownNames");
  if (!el) return;
  const names = [...new Set(Object.values(NAMES.users))].sort((a, b) => a.localeCompare(b));
  setHTML(el, names.map((n) => `<option value="${esc(n)}">`).join(""));
}

async function resolveNames() {
  if (namesInFlight) return;
  const users = new Set(), guilds = new Set();
  document.querySelectorAll("[data-uid]").forEach((el) => { if (!NAMES.users[el.dataset.uid]) users.add(el.dataset.uid); });
  document.querySelectorAll("[data-gid]").forEach((el) => { if (!NAMES.guilds[el.dataset.gid]) guilds.add(el.dataset.gid); });
  applyNames();
  if (!users.size && !guilds.size) return;
  namesInFlight = true;
  try {
    const res = await api("/api/names", { method: "POST", body: JSON.stringify({ users: [...users], guilds: [...guilds] }) });
    Object.assign(NAMES.users, res.users || {});
    Object.assign(NAMES.guilds, res.guilds || {});
    applyNames();
  } catch { /* Namen sind optional — IDs bleiben sichtbar */ }
  finally { namesInFlight = false; }
}

/* Eingabe (ID oder Name) zu einer User-ID auflösen */
async function resolveUserInput(raw) {
  if (/^\d+$/.test(raw)) return raw;
  const local = Object.entries(NAMES.users).find(([, n]) => n.toLowerCase() === raw.toLowerCase());
  if (local) return local[0];
  const res = await api(`/api/names/search?q=${encodeURIComponent(raw)}`);
  const hit = (res.results || []).find((r) => r.kind === "user");
  if (!hit) throw new Error(`Kein User „${raw}“ gefunden — Name muss schon einmal im Dashboard aufgetaucht sein.`);
  return hit.id;
}

/* ============================ Charts ============================ */

Chart.defaults.color = "#8fa0c2";
Chart.defaults.borderColor = "rgba(34,48,82,.6)";
Chart.defaults.font.family = "'Inter', sans-serif";

const PALETTE = ["#5b8cff", "#8b5cf6", "#34d399", "#fbbf24", "#f87171", "#38bdf8", "#fb923c", "#a3e635", "#e879f9", "#2dd4bf"];

function renderChart(id, type, labels, values, opts = {}) {
  const canvas = $(id);
  if (!canvas) return;
  const box = canvas.parentElement;
  const empty = !labels || !labels.length || !values.some((v) => Number(v) > 0);
  if (box && box.classList.contains("chart-box")) box.classList.toggle("empty", empty);
  const existing = state.charts[id];
  if (empty) {
    if (existing) { existing.destroy(); delete state.charts[id]; }
    return;
  }
  if (existing && existing.config.type === type) {
    // Nur Daten austauschen — keine Neu-Animation beim Auto-Refresh.
    existing.data.labels = labels;
    existing.data.datasets[0].data = values;
    existing.update("none");
    return;
  }
  if (existing) existing.destroy();
  const horizontal = opts.horizontal;
  state.charts[id] = new Chart(canvas, {
    type,
    data: {
      labels,
      datasets: [{
        label: opts.label || "",
        data: values,
        backgroundColor: type === "doughnut" ? PALETTE : "rgba(91,140,255,.55)",
        borderColor: type === "line" ? "#5b8cff" : (type === "doughnut" ? "#0e1420" : "rgba(91,140,255,.9)"),
        borderWidth: type === "line" ? 2 : 1,
        borderRadius: type === "bar" ? 5 : 0,
        fill: type === "line" ? { target: "origin", above: "rgba(91,140,255,.12)" } : undefined,
        tension: 0.35,
        pointRadius: 2,
      }],
    },
    options: {
      indexAxis: horizontal ? "y" : "x",
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 500 },
      plugins: {
        legend: { display: type === "doughnut", position: "right" },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = typeof ctx.parsed === "object" ? (horizontal ? ctx.parsed.x : ctx.parsed.y) : ctx.parsed;
              return ` ${fmt(v)}`;
            },
          },
        },
      },
      scales: type === "doughnut" ? {} : {
        x: { grid: { display: false } },
        y: { beginAtZero: true, ticks: { precision: 0 } },
      },
    },
  });
}

function chartFromPairs(id, type, pairs, opts = {}) {
  renderChart(id, type, (pairs || []).map((p) => p.label), (pairs || []).map((p) => p.value), opts);
}

function barList(containerId, rows, labelKey, valueKey, labelHTML) {
  const el = $(containerId);
  if (!el) return;
  if (!rows || !rows.length) { setHTML(el, '<div class="empty">Keine Daten</div>'); return; }
  const max = Math.max(...rows.map((r) => Number(r[valueKey]) || 0), 1);
  const labelOf = labelHTML || ((r) => `<span title="${esc(r[labelKey])}">${esc(r[labelKey])}</span>`);
  setHTML(el, rows.map((r) => `
    <div class="bar-row">
      <span class="bar-label mono">${labelOf(r)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(100 * (Number(r[valueKey]) || 0) / max).toFixed(1)}%"></div></div>
      <span class="bar-value">${fmt(r[valueKey])}</span>
    </div>`).join(""));
}

function tableHTML(headers, rows) {
  if (!rows || !rows.length) return '<div class="empty">Keine Daten</div>';
  return `<table class="data"><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((cells) => `<tr>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

/* ============================ Health ============================ */

function sessionItem(s, withAction) {
  const btn = withAction ? `<button class="mini-btn" data-end-session="${s.session_id}" title="Session als beendet markieren">✕</button>` : "";
  return `<div class="stack-item mono row"><span>Session #${s.session_id} · ${esc(s.kind || "?")} · ${statusBadge(s.status)}${s.guild_id ? ` · ${guildLabel(s.guild_id)}` : ""} · ${agoSpan(s.updated_at)}</span>${btn}</div>`;
}

function threadItem(t, withAction) {
  const btn = withAction ? `<button class="mini-btn" data-close-thread="${t.thread_id}" title="Thread schließen (Discord + DB)">🗑</button>` : "";
  return `<div class="stack-item mono row"><span>Thread ${t.thread_id} · ${esc(t.kind || "?")} · ${statusBadge(t.status)}${t.guild_id ? ` · ${guildLabel(t.guild_id)}` : ""} · ${agoSpan(t.updated_at)}</span>${btn}</div>`;
}

/* Aktive Einträge immer zeigen; abgeschlossene hinter einem Aufklapp-Button */
function splitList(items, isActive, renderItem, toggleKey, activeActionOnly = true) {
  const active = items.filter(isActive);
  const done = items.filter((i) => !isActive(i));
  const open = !!state.showDone[toggleKey];
  let html = active.map((i) => renderItem(i, true)).join("");
  if (done.length) {
    html += `<button class="toggle-done" data-toggle-done="${toggleKey}">${open ? "▾" : "▸"} ${done.length} abgeschlossene ${open ? "verbergen" : "anzeigen"}</button>`;
    if (open) html += done.map((i) => renderItem(i, !activeActionOnly)).join("");
  }
  return html;
}

function renderHealthLists(h) {
  const isActiveS = (s) => ACTIVE_STATUSES.has(String(s.status || "").toLowerCase());
  const isActiveT = (t) => String(t.status || "").toLowerCase() === "active";
  const sessions = splitList(h.active_sessions || [], isActiveS, sessionItem, "healthSessions");
  const threads = splitList(h.managed_threads || [], isActiveT, (t) => threadItem(t, true), "healthThreads", false);
  setHTML("healthSessions", (sessions + threads) || '<div class="empty">Keine aktiven Sessions oder Threads</div>');
}

async function loadHealth() {
  const h = await api("/api/health");
  state.cache.health = h;
  const dot = $("botStatusDot"), text = $("botStatusText");
  dot.className = "dot " + (h.online ? "online" : "offline");
  text.textContent = h.online
    ? `Bot online${h.uptime_seconds ? ` · ${fmtDuration(h.uptime_seconds)}` : ""}`
    : "Bot offline";

  const online = $("kpiOnline");
  online.textContent = h.online ? "Online" : "Offline";
  online.className = "kpi-value " + (h.online ? "good" : "bad");
  $("kpiOnlineSub").textContent = h.online_source === "heartbeat"
    ? `Heartbeat: ${fmtAgo(h.heartbeat_at)}`
    : (h.last_log_epoch ? `Letztes Log: ${fmtEpoch(h.last_log_epoch)} (Heuristik — Bot-Update für Heartbeat nötig)` : "kein Log gefunden");
  $("kpiUptime").textContent = fmtDuration(h.uptime_seconds);
  $("kpiRestart").textContent = h.last_startup_epoch ? `Letzter Start: ${fmtEpoch(h.last_startup_epoch)}` : "";
  const errEl = $("kpiErrors");
  errEl.textContent = fmt(h.errors_24h);
  errEl.className = "kpi-value " + (h.errors_24h > 0 ? "bad" : "good");
  errEl.closest(".kpi").classList.add("clickable");
  errEl.closest(".kpi").title = "Klick: Fehler im Log-Viewer anzeigen";
  $("kpiWarnings").textContent = `${fmt(h.warnings_24h)} Warnungen`;
  $("kpiDbSize").textContent = fmtBytes(h.db_size_bytes);
  $("kpiEvents").textContent = `${fmt(h.total_events)} Events gesamt`;
  const activeCount = (h.active_sessions || []).filter((s) => ACTIVE_STATUSES.has(String(s.status || "").toLowerCase())).length;
  $("kpiSessions").textContent = fmt(activeCount);
  $("kpiThreads").textContent = `${fmt(h.managed_thread_count)} verwaltete Threads`;
  $("kpiAfk").textContent = fmt(h.afk_timer_count);
  $("kpiFights").textContent = `${fmt(h.open_fight_requests)} offene Kampf-Anfragen`;

  setHTML("lastErrors", (h.last_errors && h.last_errors.length)
    ? h.last_errors.map((e) => `
        <div class="stack-item">
          <span class="lvl lvl-ERROR">ERROR</span>
          <span class="log-ts mono">${esc(e.timestamp)}</span><br>${esc(e.message)}
        </div>`).join("")
    : '<div class="empty">Keine Fehler in den letzten 24 h 🎉</div>');

  renderHealthLists(h);
}

async function loadLogs() {
  const params = new URLSearchParams({ limit: "300" });
  const level = $("logLevel").value, q = $("logSearch").value.trim();
  if (level) params.set("level", level);
  if (q) params.set("q", q);
  const data = await api(`/api/logs?${params}`);
  $("logMeta").textContent = data.available ? `· ${fmtBytes(data.file_size)} · ${data.entries.length} Einträge` : "· bot.log nicht gefunden";
  setHTML("logView", (data.entries || []).map((e) => `
    <div class="log-line">
      <span class="log-ts">${esc(e.timestamp)}</span><span class="lvl lvl-${esc(e.level)}">${esc(e.level)}</span>${esc(e.message)}
      ${e.detail ? `<div class="log-detail">${esc(e.detail.trim())}</div>` : ""}
    </div>`).join("") || '<div class="empty">Keine Log-Einträge</div>');
}

/* ============================ Players ============================ */

async function loadPlayers() {
  const p = await api(`/api/players?range=${state.range}`);
  $("pKpiActive").textContent = fmt(p.active_players);
  $("pKpiTotal").textContent = fmt(p.total_players);
  $("pKpiDust").textContent = fmt(p.total_dust);
  $("pKpiDustHolders").textContent = `${fmt(p.dust_holders)} Besitzer`;
  $("pKpiUnits").textContent = fmt(p.total_units);
  $("pKpiUnitHolders").textContent = `${fmt(p.unit_holders)} Besitzer`;
  $("pKpiDaily").textContent = fmt(p.daily_users_24h);
  $("pKpiTeams").textContent = fmt(p.team_count);

  chartFromPairs("chartCardDist", "bar", (p.card_distribution || []).map((r) => ({ label: r.karten_name, value: r.total })), { horizontal: true });
  chartFromPairs("chartTeamSizes", "doughnut", p.team_sizes);

  const byUser = (r) => userLabel(r.user_id);
  barList("topDust", p.top_dust, "user_id", "amount", byUser);
  barList("topUnits", p.top_units, "user_id", "amount", byUser);
  barList("topCards", p.top_cards, "user_id", "total", byUser);
  barList("mostActive", p.most_active, "user_id", "events", byUser);

  setHTML("tradingList", tableHTML(
    ["Code", "Verkäufer", "Karte", "Preis", "Zeit"],
    (p.tradingpost || []).map((t) => [
      `<span class="mono">${esc(t.code)}</span>`, `<span class="mono">${userLabel(t.seller_id)}</span>`,
      esc(t.card_name), fmt(t.preis), fmtEpoch(t.timestamp),
    ])));
}

async function lookupUser() {
  const raw = $("userLookup").value.trim();
  if (!raw) { toast("User-ID oder Namen eingeben.", true); return; }
  try {
    const id = await resolveUserInput(raw);
    const u = await api(`/api/user/${id}`);
    const cards = u.cards.length
      ? tableHTML(["Karte", "Anzahl"], u.cards.map((c) => [esc(c.karten_name), fmt(c.anzahl)]))
      : '<div class="empty">Keine Karten</div>';
    setHTML("userDetail", `
      <div class="stack-item row"><span>👤 <b>${userLabel(id)}</b> <span class="muted mono">${esc(id)}</span></span>
        <button class="btn small" data-open-detail="${esc(id)}">🔎 Ultra-Detail öffnen</button></div>
      <div class="stack-item">💠 Dust: <b>${fmt(u.dust)}</b> &nbsp;·&nbsp; 🪖 Units: <b>${fmt(u.units)}</b>
        &nbsp;·&nbsp; 🧩 Team: <span class="mono">${esc(JSON.stringify(u.team))}</span>
        &nbsp;·&nbsp; 📅 Missionen heute: <b>${fmt(u.daily ? u.daily.mission_count : 0)}</b></div>
      ${u.buffs.length ? `<div class="stack-item">Buffs: ${u.buffs.map((b) => `${esc(b.card_name)} (${esc(b.buff_type)} #${b.attack_number}: +${b.buff_amount})`).join(", ")}</div>` : ""}
      ${cards}`);
    resolveNames();
  } catch (err) { toast(String(err.message || err), true); }
}

/* ============================ Battles ============================ */

async function loadBattles() {
  const b = await api(`/api/battles?range=${state.range}`);
  state.cache.battles = b;
  $("bKpiFights").textContent = fmt(b.fight_results);
  $("bKpiAttacks").textContent = fmt(b.attacks);
  $("bKpiBug").textContent = fmt(b.feedback.bug);
  $("bKpiNoBug").textContent = `${fmt(b.feedback.no_bug)}× „kein Bug“`;
  const fr = (b.fight_requests || []).reduce((a, r) => a + r.count, 0);
  const mr = (b.mission_requests || []).reduce((a, r) => a + r.count, 0);
  $("bKpiFightReq").textContent = fmt(fr);
  $("bKpiMissionReq").textContent = `${fmt(mr)} Missions-Anfragen`;

  chartFromPairs("chartHeroes", "bar", b.top_heroes, { horizontal: true });
  chartFromPairs("chartAttacks", "bar", b.top_attacks, { horizontal: true });
  chartFromPairs("chartKinds", "doughnut", b.session_kinds);

  setHTML("winrates", tableHTML(
    ["Held", "Siege", "Niederlagen", "Win-Rate"],
    (b.winrates || []).map((w) => [esc(w.hero), fmt(w.wins), fmt(w.losses), `<b>${w.winrate}%</b>`])));

  setHTML("afkList", (b.afk_timers || []).length
    ? b.afk_timers.map((t) => `<div class="stack-item mono">${esc(t.kind)} · ${esc(t.battle_id)} · Runde ${t.round_number}${t.active_player_id ? ` · am Zug: ${userLabel(t.active_player_id)}` : ""} · ${agoSpan(t.last_action_at)}</div>`).join("")
    : '<div class="empty">Keine AFK-Timer</div>');

  const isActiveS = (s) => ACTIVE_STATUSES.has(String(s.status || "").toLowerCase());
  setHTML("battleSessions",
    splitList(b.active_sessions || [], isActiveS, sessionItem, "battleSessions")
    || '<div class="empty">Keine laufenden Sessions</div>');
}

/* ============================ Analytics ============================ */

async function loadAnalytics() {
  const a = await api(`/api/analytics?range=${state.range}`);
  chartFromPairs("chartPerDay", "line", a.per_day);
  chartFromPairs("chartPerHour", "bar", a.per_hour);
  chartFromPairs("chartCommands", "bar", a.top_commands, { horizontal: true });
  chartFromPairs("chartEventTypes", "doughnut", a.event_types);

  barList("inviteTop", a.invite_top, "user_id", "completed", (r) => userLabel(r.user_id));

  setHTML("invitePending", (a.invite_pending || []).length
    ? a.invite_pending.map((i) => `<div class="stack-item mono">#${i.id} · ${userLabel(i.inviter_id)} → ${userLabel(i.invitee_id)} · ${agoSpan(i.created_at)}</div>`).join("")
    : '<div class="empty">Keine offenen Invites</div>');

  setHTML("dustAudit", (a.dust_audit || []).length
    ? a.dust_audit.map((d) => `<div class="stack-item mono">${esc(d.action)}/${esc(d.mode)} · ${userLabel(d.actor_id)} → ${userLabel(d.target_id)} · ${fmt(d.applied_amount)} · ${agoSpan(d.created_at)}</div>`).join("")
    : '<div class="empty">Keine Einträge</div>');
}

/* ============================ Spieler-Analyse (Ultra-Detail) ============================ */

async function analyzeUser(raw) {
  raw = (raw || $("detailSearch").value).trim();
  if (!raw) { toast("User-ID oder Namen eingeben.", true); return; }
  try {
    state.detailUser = await resolveUserInput(raw);
    await loadDetailTab();
  } catch (err) { toast(String(err.message || err), true); }
}

async function loadDetailTab() {
  if (!state.detailUser) return;
  const d = await api(`/api/user/${state.detailUser}/full`);
  renderDetail(d);
  resolveNames();
}

function renderDetail(d) {
  $("detailHint").style.display = "none";
  $("detailBody").style.display = "";
  const id = d.user_id;

  setHTML("detailProfile", `
    <div class="profile-head">
      <div class="profile-avatar">👤</div>
      <div>
        <div class="profile-name">${userLabel(id)}</div>
        <div class="profile-meta mono">ID ${esc(id)}
          · Erste Aktivität: <span title="${fmtEpoch(d.first_seen)}">${d.first_seen ? fmtEpoch(d.first_seen) : "unbekannt"}</span>
          · Zuletzt aktiv: ${d.last_seen ? agoSpan(d.last_seen) : "–"}</div>
        ${d.guilds_seen && d.guilds_seen.length ? `<div class="profile-meta">Aktiv auf: ${d.guilds_seen.map((g) => guildLabel(g)).join(", ")}</div>` : ""}
      </div>
      <div class="profile-tags">
        <span class="tag">🧩 Team: <b>${d.team && d.team.length ? esc(d.team.join(", ")) : "keins"}</b></span>
        <span class="tag">📅 Missionen heute: <b>${fmt(d.daily ? d.daily.mission_count : 0)}</b></span>
        ${d.daily && d.daily.last_daily ? `<span class="tag">Letztes Daily: <b>${fmtAgo(d.daily.last_daily)}</b></span>` : ""}
      </div>
    </div>`);

  $("dKpiEvents").textContent = fmt(d.events_total);
  $("dKpiEventsSub").textContent = d.events_total > d.events_fetched ? `Auswertung: letzte ${fmt(d.events_fetched)}` : "gesamt";
  $("dKpiFights").textContent = fmt(d.wins + d.losses);
  $("dKpiWinrate").textContent = d.winrate == null ? "keine Kämpfe" : `${d.wins}W / ${d.losses}L · ${d.winrate}% Win-Rate`;
  $("dKpiMissions").textContent = fmt(d.mission_count);
  $("dKpiMissionsSub").textContent = "Missions-Anfragen";
  $("dKpiDust").textContent = fmt(d.dust);
  $("dKpiUnits").textContent = `🪖 ${fmt(d.units)} Units`;
  const totalCards = (d.cards || []).reduce((a, c) => a + (c.anzahl || 0), 0);
  $("dKpiCards").textContent = fmt(totalCards);
  $("dKpiCardsSub").textContent = `${fmt((d.cards || []).length)} verschiedene`;
  $("dKpiInvites").textContent = fmt(d.invites_completed);

  renderChart("chartUserActivity", "line", (d.timeline || []).map((p) => p.label), (d.timeline || []).map((p) => p.value));
  barList("detailCommands", d.top_commands, "label", "value");
  barList("detailHeroes", d.top_heroes, "label", "value");
  barList("detailEventTypes", d.event_types, "label", "value");

  setHTML("detailFights", (d.fights || []).length
    ? d.fights.map((f) => `<div class="stack-item mono ${f.won ? "win" : "loss"}">${f.won ? "🏆 Sieg" : "💀 Niederlage"} · ${esc(f.own_hero)} vs ${esc(f.opp_hero)} · gegen ${f.opponent_id === "0" ? "🤖 Bot" : userLabel(f.opponent_id)}${f.rounds ? ` · ${f.rounds} Runden` : ""} · ${esc(f.kind)} · ${agoSpan(f.created_at)}</div>`).join("")
    : '<div class="empty">Keine Kämpfe aufgezeichnet</div>');
  $("detailFightsMeta").textContent = d.fights && d.fights.length ? `· ${d.fights.length} angezeigt` : "";

  setHTML("detailMissions", (d.missions || []).length
    ? d.missions.map((m) => `<div class="stack-item mono">#${m.id}${m.name ? ` · ${esc(m.name)}` : ""} · ${statusBadge(m.status)}${m.guild_id !== "0" ? ` · ${guildLabel(m.guild_id)}` : ""} · <span title="${fmtEpoch(m.created_at)}">${fmtEpoch(m.created_at)}</span></div>`).join("")
    : '<div class="empty">Keine Missionen aufgezeichnet</div>');
  $("detailMissionsMeta").textContent = d.missions && d.missions.length ? `· ${d.missions.length} angezeigt` : "";

  setHTML("detailInvites", (d.invite_history || []).length
    ? d.invite_history.map((i) => `<div class="stack-item mono">${userLabel(i.inviter_id)} → ${userLabel(i.invitee_id)} · ${statusBadge(i.status)} · ${agoSpan(i.created_at)}</div>`).join("")
    : '<div class="empty">Keine Invite-Historie</div>');

  setHTML("detailTrading", (d.trading || []).length
    ? d.trading.map((t) => `<div class="stack-item mono">${esc(t.code)} · ${esc(t.card_name)} · ${fmt(t.preis)} 💠 · ${fmtEpoch(t.timestamp)}</div>`).join("")
    : '<div class="empty">Keine Trading-Angebote</div>');

  setHTML("detailDustAudit", (d.dust_audit || []).length
    ? d.dust_audit.map((a) => `<div class="stack-item mono">${esc(a.action)}/${esc(a.mode)} · ${userLabel(a.actor_id)} → ${userLabel(a.target_id)} · ${fmt(a.applied_amount)} · ${agoSpan(a.created_at)}</div>`).join("")
    : '<div class="empty">Keine Admin-Dust-Einträge</div>');

  const cardsTable = (d.cards || []).length
    ? tableHTML(["Karte", "Anzahl"], d.cards.map((c) => [esc(c.karten_name), fmt(c.anzahl)]))
    : '<div class="empty">Keine Karten</div>';
  setHTML("detailInventory", `
    ${d.buffs && d.buffs.length ? `<div class="stack-item">Buffs: ${d.buffs.map((b) => `${esc(b.card_name)} (${esc(b.buff_type)} #${b.attack_number}: +${b.buff_amount})`).join(", ")}</div>` : ""}
    ${cardsTable}`);

  setHTML("detailEvents", (d.recent_events || []).length
    ? d.recent_events.map((e) => {
        const what = e.command_name ? `/${esc(e.command_name)}`
          : (e.hero_name ? `${esc(e.hero_name)}${e.attack_name ? ` — ${esc(e.attack_name)}` : ""}` : "");
        return `<div class="stack-item mono"><span class="log-ts">${fmtEpoch(e.created_at)}</span> ${esc(e.event_type)}${what ? ` · ${what}` : ""}${e.session_kind ? ` · ${esc(e.session_kind)}` : ""}</div>`;
      }).join("")
    : '<div class="empty">Keine Events</div>');
  $("detailEventsMeta").textContent = d.recent_events && d.recent_events.length ? `· letzte ${d.recent_events.length}` : "";
}

/* ============================ Admin ============================ */

let META = { cards: [], admin_enabled: false, names_enabled: false };

async function refreshAdminUI() {
  const status = await api("/api/admin/status");
  const locked = $("adminLocked"), panel = $("adminPanel");
  if (!status.admin_enabled) {
    locked.style.display = "";
    panel.style.display = "none";
    $("adminLockedMsg").innerHTML = "⚠️ Admin-Aktionen sind deaktiviert, weil <code>DASHBOARD_PASSWORD</code> nicht gesetzt ist. In <code>website/.env</code> setzen und neu starten.";
    return;
  }
  if (status.authenticated) {
    locked.style.display = "none";
    panel.style.display = "";
    await Promise.all([loadGuildFlags(), loadAudit(), loadTradingAdmin()]);
  } else {
    locked.style.display = "";
    panel.style.display = "none";
  }
}

async function adminLogin() {
  try {
    await api("/api/admin/login", { method: "POST", body: JSON.stringify({ password: $("adminPassword").value }) });
    $("adminPassword").value = "";
    toast("Eingeloggt ✔");
    await refreshAdminUI();
  } catch (err) { toast(String(err.message || err), true); }
}

async function loadGuildFlags() {
  try {
    const guilds = await api("/api/admin/guilds");
    const flags = ["maintenance_mode", "beta_enabled", "alpha_enabled"];
    const changed = setHTML("guildFlags", guilds.length
      ? guilds.map((g) => `
        <div class="flag-row">
          <span class="flag-guild">${guildLabel(g.guild_id, true)}</span>
          ${flags.map((f) => `
            <label class="flag-toggle">
              <input type="checkbox" data-guild="${esc(g.guild_id)}" data-flag="${f}" ${g[f] ? "checked" : ""}>
              ${f.replace("_enabled", "").replace("_mode", "")}
            </label>`).join("")}
        </div>`).join("")
      : '<div class="empty">Noch keine Guild-Konfigurationen. Guild-ID oben eingeben und einen Schalter setzen.</div>');
    if (changed) {
      document.querySelectorAll('#guildFlags input[type="checkbox"]').forEach((box) => {
        box.addEventListener("change", () => setGuildFlag(box.dataset.guild, box.dataset.flag, box.checked, box));
      });
    }
    resolveNames();
  } catch (err) { toast(String(err.message || err), true); }
}

async function setGuildFlag(guildId, flag, enabled, box) {
  const ok = await confirmAction(`Flag „${flag}“ für Guild ${guildId} ${enabled ? "AKTIVIEREN" : "deaktivieren"}?`);
  if (!ok) { if (box) box.checked = !enabled; return; }
  try {
    await api("/api/admin/guild-flag", { method: "POST", body: JSON.stringify({ guild_id: guildId, flag, enabled }) });
    toast(`${flag} für ${guildId}: ${enabled ? "an" : "aus"} ✔`);
    await loadAudit();
  } catch (err) {
    if (box) box.checked = !enabled;
    toast(String(err.message || err), true);
  }
}

/* "user:123" / "guild:456" / "session:7" / "thread:9" im Audit-Ziel hübsch anzeigen */
function auditTarget(target) {
  const m = /^(user|guild|session|thread):(\d+)$/.exec(String(target || ""));
  if (!m) return esc(target);
  if (m[1] === "user") return userLabel(m[2]);
  if (m[1] === "guild") return guildLabel(m[2]);
  return `${m[1]} ${m[2]}`;
}

async function loadAudit() {
  try {
    const rows = await api("/api/admin/audit?limit=100");
    setHTML("auditLog", tableHTML(
      ["Zeit", "Aktion", "Ziel", "Betrag", "Detail"],
      rows.map((r) => [fmtEpoch(r.created_at), esc(r.action), `<span class="mono">${auditTarget(r.target)}</span>`,
        r.amount == null ? "–" : fmt(r.amount), esc(r.detail || "")])));
    resolveNames();
  } catch (err) { toast(String(err.message || err), true); }
}

async function loadTradingAdmin() {
  try {
    const p = await api(`/api/players?range=all`);
    setHTML("tpAdminList", (p.tradingpost || []).length
      ? p.tradingpost.map((t) => `<div class="stack-item mono">${esc(t.code)} · ${esc(t.card_name)} · ${fmt(t.preis)} · Verkäufer ${userLabel(t.seller_id)}</div>`).join("")
      : '<div class="empty">Trading-Post ist leer</div>');
  } catch { /* nur Anzeige */ }
}

async function submitCurrency() {
  const userId = $("curUserId").value.trim(), kind = $("curKind").value,
        amount = parseInt($("curAmount").value, 10), action = $("curAction").value;
  if (!/^\d+$/.test(userId) || !(amount > 0)) { toast("User-ID und Betrag prüfen.", true); return; }
  const label = kind === "dust" ? "InfinityDust" : "Units";
  const who = NAMES.users[userId] ? `${NAMES.users[userId]} (${userId})` : `User ${userId}`;
  if (!(await confirmAction(`${action === "give" ? "GEBEN" : "ABZIEHEN"}: ${fmt(amount)} ${label} für ${who}?`))) return;
  try {
    const res = await api("/api/admin/currency", { method: "POST", body: JSON.stringify({ kind, user_id: userId, amount, action }) });
    toast(`OK — angewendet: ${fmt(res.applied)}, neuer Stand: ${fmt(res.new_amount)}`);
    await loadAudit();
  } catch (err) { toast(String(err.message || err), true); }
}

async function submitCard() {
  const userId = $("cardUserId").value.trim(), name = $("cardName").value.trim(),
        amount = parseInt($("cardAmount").value, 10), action = $("cardAction").value;
  if (!/^\d+$/.test(userId) || !name || !(amount > 0)) { toast("Eingaben prüfen.", true); return; }
  const who = NAMES.users[userId] ? `${NAMES.users[userId]} (${userId})` : `User ${userId}`;
  if (!(await confirmAction(`${action === "give" ? "GEBEN" : "ENTFERNEN"}: ${amount}× „${name}“ für ${who}?`))) return;
  try {
    const res = await api("/api/admin/card", { method: "POST", body: JSON.stringify({ user_id: userId, card_name: name, amount, action }) });
    toast(`OK — ${res.applied}× ${res.card}`);
    await loadAudit();
  } catch (err) { toast(String(err.message || err), true); }
}

async function deleteTrading() {
  const code = $("tpCode").value.trim();
  if (!code) { toast("Code eingeben.", true); return; }
  if (!(await confirmAction(`Trading-Post-Eintrag „${code}“ unwiderruflich löschen?`))) return;
  try {
    await api("/api/admin/tradingpost/delete", { method: "POST", body: JSON.stringify({ code }) });
    toast("Eintrag gelöscht ✔");
    await Promise.all([loadAudit(), loadTradingAdmin()]);
  } catch (err) { toast(String(err.message || err), true); }
}

async function runCleanup(what) {
  const names = { sessions: "Sessions", threads: "Threads", afk: "AFK-Timer" };
  if (!(await confirmAction(`${names[what]} aufräumen? Entfernt beendete bzw. über 24 h alte Einträge.`))) return;
  try {
    const res = await api("/api/admin/cleanup", { method: "POST", body: JSON.stringify({ what }) });
    toast(`${names[what]}: ${fmt(res.removed)} Einträge entfernt`);
    await loadAudit();
  } catch (err) { toast(String(err.message || err), true); }
}

/* ---------- Session-/Thread-Aktionen (Buttons in den Listen) ---------- */

async function endSession(sessionId) {
  if (!(await confirmAction(`Session #${sessionId} als beendet markieren?`))) return;
  try {
    await api("/api/admin/session/end", { method: "POST", body: JSON.stringify({ session_id: sessionId }) });
    toast(`Session #${sessionId} beendet ✔`);
    await loadTab(state.tab);
  } catch (err) {
    toast(err.message && err.message.includes("401") ? "Bitte zuerst im Admin-Tab einloggen." : String(err.message || err), true);
  }
}

async function closeThread(threadId) {
  if (!(await confirmAction(`Thread ${threadId} schließen? Der Thread wird in Discord GELÖSCHT (falls BOT_TOKEN gesetzt) und in der DB als gelöscht markiert.`))) return;
  try {
    const res = await api("/api/admin/thread/close", { method: "POST", body: JSON.stringify({ thread_id: threadId, delete_discord: true }) });
    toast(`Thread ${threadId} geschlossen ✔${res.discord_status === 404 ? " (existierte in Discord nicht mehr)" : ""}`);
    await loadTab(state.tab);
  } catch (err) {
    toast(err.message && err.message.includes("401") ? "Bitte zuerst im Admin-Tab einloggen." : String(err.message || err), true);
  }
}

/* ============================ Update-Check ============================ */

let UPDATE_INFO = null;

async function checkUpdate(force = false) {
  try {
    UPDATE_INFO = await api(`/api/update-check${force ? "?force=true" : ""}`);
    const badge = $("updateBadge");
    if (UPDATE_INFO.update_available) {
      badge.style.display = "";
      badge.textContent = `⬆ Update v${UPDATE_INFO.latest} verfügbar`;
    } else {
      badge.style.display = "none";
    }
  } catch { /* Update-Check ist optional */ }
  return UPDATE_INFO;
}

function openUpdateModal() {
  const i = UPDATE_INFO || {};
  const current = i.current || (META.version || "?");
  let body;
  if (i.update_available) {
    body = `
      <p>Installiert: <b>v${esc(current)}</b> &nbsp;→&nbsp; Neueste Version: <b class="good">v${esc(i.latest)}</b></p>
      <p><b>So updatest du auf ZimaOS:</b></p>
      <ol style="margin: 0 0 14px 18px; color: var(--muted); font-size: 13px; line-height: 1.7">
        <li>ZimaOS öffnen → App <b>kartenbot-dashboard</b> auswählen</li>
        <li>„Update prüfen“ / Image neu ziehen (<code>ghcr.io/bastild/kartenbot-dashboard:latest</code>)<br>
            <span class="muted">Falls ZimaOS kein Update anbietet: Image-Tag auf <code>:${esc(i.latest)}</code> ändern — das erzwingt den Pull.</span></li>
        <li>Container neu starten</li>
        <li>Hier unten links prüfen, dass <b>v${esc(i.latest)}</b> angezeigt wird</li>
      </ol>`;
  } else if (i.latest) {
    body = `<p>Installiert: <b>v${esc(current)}</b> — du bist auf dem neuesten Stand ✔</p>`;
  } else {
    body = `<p>Installiert: <b>v${esc(current)}</b> — Update-Server nicht erreichbar (GitHub). Später erneut versuchen.</p>`;
  }
  $("updateModalBody").innerHTML = body;
  $("updateModal").classList.add("show");
}

/* ============================ Routing / Refresh ============================ */

const LOADERS = {
  health: async () => { await loadHealth(); await loadLogs(); },
  players: loadPlayers,
  battles: loadBattles,
  analytics: loadAnalytics,
  detail: loadDetailTab,
  admin: refreshAdminUI,
};

async function loadTab(tab) {
  const btn = $("refreshBtn");
  btn.classList.add("loading");
  state.loading = true;
  try {
    await LOADERS[tab]();
    $("lastUpdated").textContent = `Stand: ${new Date().toLocaleTimeString("de-DE")}`;
    resolveNames();
  } catch (err) {
    toast(`Fehler beim Laden: ${err.message || err}`, true);
  } finally {
    state.loading = false;
    btn.classList.remove("loading");
  }
}

function switchTab(tab) {
  state.tab = tab;
  localStorage.setItem("kb_tab", tab);
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === `tab-${tab}`));
  $("pageTitle").innerHTML = TAB_TITLES[tab];
  loadTab(tab);
}

function scheduleRefresh() {
  clearInterval(state.timer);
  if (state.autoRefresh) {
    state.timer = setInterval(() => {
      if (state.loading) return;
      loadTab(state.tab);
      if (state.tab !== "health") loadHealth().catch(() => {});
    }, 15000);
  }
}

async function init() {
  document.querySelectorAll(".nav-item").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  document.querySelectorAll("#rangePicker button").forEach((b) => {
    b.classList.toggle("active", b.dataset.range === state.range);
    b.addEventListener("click", () => {
      state.range = b.dataset.range;
      localStorage.setItem("kb_range", state.range);
      document.querySelectorAll("#rangePicker button").forEach((x) => x.classList.toggle("active", x === b));
      loadTab(state.tab);
    });
  });
  $("refreshBtn").addEventListener("click", () => loadTab(state.tab));
  const autoBox = $("autoRefresh");
  autoBox.checked = state.autoRefresh;
  autoBox.addEventListener("change", (e) => {
    state.autoRefresh = e.target.checked;
    localStorage.setItem("kb_auto", e.target.checked ? "1" : "0");
    scheduleRefresh();
  });
  $("logReload").addEventListener("click", loadLogs);
  $("logSearch").addEventListener("keydown", (e) => e.key === "Enter" && loadLogs());
  $("logLevel").addEventListener("change", loadLogs);
  $("userLookupBtn").addEventListener("click", lookupUser);
  $("userLookup").addEventListener("keydown", (e) => e.key === "Enter" && lookupUser());
  $("userLookup").setAttribute("list", "knownNames");
  $("detailSearchBtn").addEventListener("click", () => analyzeUser());
  $("detailSearch").addEventListener("keydown", (e) => e.key === "Enter" && analyzeUser());

  $("adminLoginBtn").addEventListener("click", adminLogin);
  $("adminPassword").addEventListener("keydown", (e) => e.key === "Enter" && adminLogin());
  $("adminLogoutBtn").addEventListener("click", async () => {
    await api("/api/admin/logout", { method: "POST" });
    toast("Ausgeloggt");
    await refreshAdminUI();
  });
  $("curSubmit").addEventListener("click", submitCurrency);
  $("cardSubmit").addEventListener("click", submitCard);
  $("tpDelete").addEventListener("click", deleteTrading);
  $("guildReload").addEventListener("click", async () => {
    const raw = $("flagGuildId").value.trim();
    if (raw && /^\d+$/.test(raw)) {
      // Neue Guild anlegen: alle Flags aus — erscheint danach in der Liste.
      await setGuildFlag(raw, "maintenance_mode", false, null);
    }
    await loadGuildFlags();
  });
  document.querySelectorAll("[data-cleanup]").forEach((b) => b.addEventListener("click", () => runCleanup(b.dataset.cleanup)));

  // Update-Check
  $("updateBadge").addEventListener("click", openUpdateModal);
  $("dashVersion").style.cursor = "pointer";
  $("dashVersion").title = "Klick: Update-Status anzeigen";
  $("dashVersion").addEventListener("click", openUpdateModal);
  $("updateClose").addEventListener("click", () => $("updateModal").classList.remove("show"));
  $("updateCheckNow").addEventListener("click", async () => {
    await checkUpdate(true);
    openUpdateModal();
  });

  // Zentrale Klick-Delegation: Session beenden, Thread schließen, Listen aufklappen,
  // Ultra-Detail öffnen, Fehler-KPI → Log-Filter
  document.addEventListener("click", (e) => {
    const end = e.target.closest("[data-end-session]");
    if (end) { endSession(parseInt(end.dataset.endSession, 10)); return; }
    const thr = e.target.closest("[data-close-thread]");
    if (thr) { closeThread(thr.dataset.closeThread); return; }
    const tog = e.target.closest("[data-toggle-done]");
    if (tog) {
      const key = tog.dataset.toggleDone;
      state.showDone[key] = !state.showDone[key];
      if (state.cache.health) renderHealthLists(state.cache.health);
      if (state.cache.battles && key === "battleSessions") {
        const isActiveS = (s) => ACTIVE_STATUSES.has(String(s.status || "").toLowerCase());
        setHTML("battleSessions", splitList(state.cache.battles.active_sessions || [], isActiveS, sessionItem, "battleSessions") || '<div class="empty">Keine laufenden Sessions</div>');
      }
      resolveNames();
      return;
    }
    const open = e.target.closest("[data-open-detail]");
    if (open) {
      $("detailSearch").value = open.dataset.openDetail;
      switchTab("detail");
      analyzeUser(open.dataset.openDetail);
      return;
    }
    if (e.target.closest("#kpiErrors")) {
      $("logLevel").value = "ERROR,WARNING";
      loadLogs();
      $("logView").scrollIntoView({ behavior: "smooth" });
    }
  });

  // Escape schließt offene Modals
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      $("updateModal").classList.remove("show");
      const c = $("confirmModal");
      if (c.classList.contains("show") && $("confirmNo").onclick) $("confirmNo").onclick();
    }
  });

  try {
    META = await api("/api/meta");
    $("cardNames").innerHTML = (META.cards || []).map((c) => `<option value="${esc(c)}">`).join("");
    if (META.names_enabled === false) {
      $("userLookup").placeholder = "Discord User-ID … (BOT_TOKEN setzen für Namen)";
      $("detailSearch").placeholder = "Discord User-ID … (BOT_TOKEN setzen für Namen)";
    }
    if (META.version) $("dashVersion").textContent = `· v${META.version}`;
  } catch { /* Meta optional */ }

  await loadHealth().catch(() => {});
  switchTab(TAB_TITLES[state.tab] ? state.tab : "health");
  scheduleRefresh();
  checkUpdate();
  setInterval(checkUpdate, 1800000); // alle 30 min auf neue Version prüfen
}

document.addEventListener("DOMContentLoaded", init);
