/* Kartenbot Dashboard — Frontend-Logik (Vanilla JS + Chart.js) */
"use strict";

const state = {
  range: "7d",
  tab: "health",
  charts: {},
  autoRefresh: true,
  timer: null,
};

const TAB_TITLES = {
  health: "Health & Logs",
  players: "Spieler & Economy",
  battles: "Battles & Missionen",
  analytics: "Commands & Invites",
  admin: "Admin",
};

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
  if (seconds == null) return "–";
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function fmtEpoch(epoch) {
  if (!epoch) return "–";
  return new Date(epoch * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
}

function toast(message, isError = false) {
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

/* ============================ Charts ============================ */

Chart.defaults.color = "#8fa0c2";
Chart.defaults.borderColor = "rgba(34,48,82,.6)";
Chart.defaults.font.family = "'Inter', sans-serif";

const PALETTE = ["#5b8cff", "#8b5cf6", "#34d399", "#fbbf24", "#f87171", "#38bdf8", "#fb923c", "#a3e635", "#e879f9", "#2dd4bf"];

function renderChart(id, type, labels, values, opts = {}) {
  const canvas = $(id);
  if (!canvas) return;
  if (state.charts[id]) state.charts[id].destroy();
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

function barList(containerId, rows, labelKey, valueKey) {
  const el = $(containerId);
  if (!el) return;
  if (!rows || !rows.length) { el.innerHTML = '<div class="empty">Keine Daten</div>'; return; }
  const max = Math.max(...rows.map((r) => Number(r[valueKey]) || 0), 1);
  el.innerHTML = rows.map((r) => `
    <div class="bar-row">
      <span class="bar-label mono" title="${esc(r[labelKey])}">${esc(r[labelKey])}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(100 * (Number(r[valueKey]) || 0) / max).toFixed(1)}%"></div></div>
      <span class="bar-value">${fmt(r[valueKey])}</span>
    </div>`).join("");
}

function tableHTML(headers, rows) {
  if (!rows || !rows.length) return '<div class="empty">Keine Daten</div>';
  return `<table class="data"><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((cells) => `<tr>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

/* ============================ Health ============================ */

async function loadHealth() {
  const h = await api("/api/health");
  const dot = $("botStatusDot"), text = $("botStatusText");
  dot.className = "dot " + (h.online ? "online" : "offline");
  text.textContent = h.online ? "Bot online" : "Bot offline";

  const online = $("kpiOnline");
  online.textContent = h.online ? "Online" : "Offline";
  online.className = "kpi-value " + (h.online ? "good" : "bad");
  $("kpiOnlineSub").textContent = h.last_log_epoch ? `Letztes Log: ${fmtEpoch(h.last_log_epoch)}` : "kein Log gefunden";
  $("kpiUptime").textContent = fmtDuration(h.uptime_seconds);
  $("kpiRestart").textContent = h.last_startup_epoch ? `Letzter Start: ${fmtEpoch(h.last_startup_epoch)}` : "";
  const errEl = $("kpiErrors");
  errEl.textContent = fmt(h.errors_24h);
  errEl.className = "kpi-value " + (h.errors_24h > 0 ? "bad" : "good");
  $("kpiWarnings").textContent = `${fmt(h.warnings_24h)} Warnungen`;
  $("kpiDbSize").textContent = fmtBytes(h.db_size_bytes);
  $("kpiEvents").textContent = `${fmt(h.total_events)} Events gesamt`;
  $("kpiSessions").textContent = fmt(h.active_session_count);
  $("kpiThreads").textContent = `${fmt(h.managed_thread_count)} verwaltete Threads`;
  $("kpiAfk").textContent = fmt(h.afk_timer_count);
  $("kpiFights").textContent = `${fmt(h.open_fight_requests)} offene Kampf-Anfragen`;

  $("lastErrors").innerHTML = (h.last_errors && h.last_errors.length)
    ? h.last_errors.map((e) => `
        <div class="stack-item">
          <span class="lvl lvl-ERROR">ERROR</span>
          <span class="log-ts mono">${esc(e.timestamp)}</span><br>${esc(e.message)}
        </div>`).join("")
    : '<div class="empty">Keine Fehler in den letzten 24 h 🎉</div>';

  const sessions = (h.active_sessions || []).map((s) =>
    `<div class="stack-item mono">Session #${s.session_id} · ${esc(s.kind || "?")} · ${esc(s.status || "?")} · ${fmtEpoch(s.updated_at)}</div>`);
  const threads = (h.managed_threads || []).map((t) =>
    `<div class="stack-item mono">Thread ${t.thread_id} · ${esc(t.kind || "?")} · ${esc(t.status || "?")}</div>`);
  $("healthSessions").innerHTML = sessions.concat(threads).join("") || '<div class="empty">Keine aktiven Sessions oder Threads</div>';
}

async function loadLogs() {
  const params = new URLSearchParams({ limit: "300" });
  const level = $("logLevel").value, q = $("logSearch").value.trim();
  if (level) params.set("level", level);
  if (q) params.set("q", q);
  const data = await api(`/api/logs?${params}`);
  $("logMeta").textContent = data.available ? `· ${fmtBytes(data.file_size)} · ${data.entries.length} Einträge` : "· bot.log nicht gefunden";
  $("logView").innerHTML = (data.entries || []).map((e) => `
    <div class="log-line">
      <span class="log-ts">${esc(e.timestamp)}</span><span class="lvl lvl-${esc(e.level)}">${esc(e.level)}</span>${esc(e.message)}
      ${e.detail ? `<div class="log-detail">${esc(e.detail.trim())}</div>` : ""}
    </div>`).join("") || '<div class="empty">Keine Log-Einträge</div>';
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

  barList("topDust", p.top_dust, "user_id", "amount");
  barList("topUnits", p.top_units, "user_id", "amount");
  barList("topCards", p.top_cards, "user_id", "total");
  barList("mostActive", p.most_active, "user_id", "events");

  $("tradingList").innerHTML = tableHTML(
    ["Code", "Verkäufer", "Karte", "Preis", "Zeit"],
    (p.tradingpost || []).map((t) => [
      `<span class="mono">${esc(t.code)}</span>`, `<span class="mono">${esc(t.seller_id)}</span>`,
      esc(t.card_name), fmt(t.preis), fmtEpoch(t.timestamp),
    ]));
}

async function lookupUser() {
  const id = $("userLookup").value.trim();
  if (!/^\d+$/.test(id)) { toast("Bitte eine numerische User-ID eingeben.", true); return; }
  try {
    const u = await api(`/api/user/${id}`);
    const cards = u.cards.length
      ? tableHTML(["Karte", "Anzahl"], u.cards.map((c) => [esc(c.karten_name), fmt(c.anzahl)]))
      : '<div class="empty">Keine Karten</div>';
    $("userDetail").innerHTML = `
      <div class="stack-item">💠 Dust: <b>${fmt(u.dust)}</b> &nbsp;·&nbsp; 🪖 Units: <b>${fmt(u.units)}</b>
        &nbsp;·&nbsp; 🧩 Team: <span class="mono">${esc(JSON.stringify(u.team))}</span>
        &nbsp;·&nbsp; 📅 Missionen heute: <b>${fmt(u.daily ? u.daily.mission_count : 0)}</b></div>
      ${u.buffs.length ? `<div class="stack-item">Buffs: ${u.buffs.map((b) => `${esc(b.card_name)} (${esc(b.buff_type)} #${b.attack_number}: +${b.buff_amount})`).join(", ")}</div>` : ""}
      ${cards}`;
  } catch (err) { toast(String(err.message || err), true); }
}

/* ============================ Battles ============================ */

async function loadBattles() {
  const b = await api(`/api/battles?range=${state.range}`);
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

  $("winrates").innerHTML = tableHTML(
    ["Held", "Siege", "Niederlagen", "Win-Rate"],
    (b.winrates || []).map((w) => [esc(w.hero), fmt(w.wins), fmt(w.losses), `<b>${w.winrate}%</b>`]));

  $("afkList").innerHTML = (b.afk_timers || []).length
    ? b.afk_timers.map((t) => `<div class="stack-item mono">${esc(t.kind)} · ${esc(t.battle_id)} · Runde ${t.round_number} · zuletzt ${fmtEpoch(t.last_action_at)}</div>`).join("")
    : '<div class="empty">Keine AFK-Timer</div>';

  $("battleSessions").innerHTML = (b.active_sessions || []).length
    ? b.active_sessions.map((s) => `<div class="stack-item mono">#${s.session_id} · ${esc(s.kind || "?")} · ${esc(s.status || "?")} · ${fmtEpoch(s.updated_at)}</div>`).join("")
    : '<div class="empty">Keine laufenden Sessions</div>';
}

/* ============================ Analytics ============================ */

async function loadAnalytics() {
  const a = await api(`/api/analytics?range=${state.range}`);
  chartFromPairs("chartPerDay", "line", a.per_day);
  chartFromPairs("chartPerHour", "bar", a.per_hour);
  chartFromPairs("chartCommands", "bar", a.top_commands, { horizontal: true });
  chartFromPairs("chartEventTypes", "doughnut", a.event_types);

  barList("inviteTop", a.invite_top, "user_id", "completed");

  $("invitePending").innerHTML = (a.invite_pending || []).length
    ? a.invite_pending.map((i) => `<div class="stack-item mono">#${i.id} · ${esc(i.inviter_id)} → ${esc(i.invitee_id)} · ${fmtEpoch(i.created_at)}</div>`).join("")
    : '<div class="empty">Keine offenen Invites</div>';

  $("dustAudit").innerHTML = (a.dust_audit || []).length
    ? a.dust_audit.map((d) => `<div class="stack-item mono">${esc(d.action)}/${esc(d.mode)} · ${esc(d.actor_id)} → ${esc(d.target_id)} · ${fmt(d.applied_amount)} · ${fmtEpoch(d.created_at)}</div>`).join("")
    : '<div class="empty">Keine Einträge</div>';
}

/* ============================ Admin ============================ */

let META = { cards: [], admin_enabled: false };

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
    $("guildFlags").innerHTML = guilds.length
      ? guilds.map((g) => `
        <div class="flag-row">
          <span class="flag-guild">${esc(g.guild_id)}</span>
          ${flags.map((f) => `
            <label class="flag-toggle">
              <input type="checkbox" data-guild="${esc(g.guild_id)}" data-flag="${f}" ${g[f] ? "checked" : ""}>
              ${f.replace("_enabled", "").replace("_mode", "")}
            </label>`).join("")}
        </div>`).join("")
      : '<div class="empty">Noch keine Guild-Konfigurationen. Guild-ID oben eingeben und einen Schalter setzen.</div>';
    document.querySelectorAll('#guildFlags input[type="checkbox"]').forEach((box) => {
      box.addEventListener("change", () => setGuildFlag(box.dataset.guild, box.dataset.flag, box.checked, box));
    });
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

async function loadAudit() {
  try {
    const rows = await api("/api/admin/audit?limit=100");
    $("auditLog").innerHTML = tableHTML(
      ["Zeit", "Aktion", "Ziel", "Betrag", "Detail"],
      rows.map((r) => [fmtEpoch(r.created_at), esc(r.action), `<span class="mono">${esc(r.target)}</span>`,
        r.amount == null ? "–" : fmt(r.amount), esc(r.detail || "")]));
  } catch (err) { toast(String(err.message || err), true); }
}

async function loadTradingAdmin() {
  try {
    const p = await api(`/api/players?range=all`);
    $("tpAdminList").innerHTML = (p.tradingpost || []).length
      ? p.tradingpost.map((t) => `<div class="stack-item mono">${esc(t.code)} · ${esc(t.card_name)} · ${fmt(t.preis)} · Verkäufer ${esc(t.seller_id)}</div>`).join("")
      : '<div class="empty">Trading-Post ist leer</div>';
  } catch { /* nur Anzeige */ }
}

async function submitCurrency() {
  const userId = $("curUserId").value.trim(), kind = $("curKind").value,
        amount = parseInt($("curAmount").value, 10), action = $("curAction").value;
  if (!/^\d+$/.test(userId) || !(amount > 0)) { toast("User-ID und Betrag prüfen.", true); return; }
  const label = kind === "dust" ? "InfinityDust" : "Units";
  if (!(await confirmAction(`${action === "give" ? "GEBEN" : "ABZIEHEN"}: ${fmt(amount)} ${label} für User ${userId}?`))) return;
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
  if (!(await confirmAction(`${action === "give" ? "GEBEN" : "ENTFERNEN"}: ${amount}× „${name}“ für User ${userId}?`))) return;
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

/* ============================ Routing / Refresh ============================ */

const LOADERS = {
  health: async () => { await loadHealth(); await loadLogs(); },
  players: loadPlayers,
  battles: loadBattles,
  analytics: loadAnalytics,
  admin: refreshAdminUI,
};

async function loadTab(tab) {
  try {
    await LOADERS[tab]();
  } catch (err) {
    toast(`Fehler beim Laden: ${err.message || err}`, true);
  }
}

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === `tab-${tab}`));
  $("pageTitle").innerHTML = TAB_TITLES[tab];
  loadTab(tab);
}

function scheduleRefresh() {
  clearInterval(state.timer);
  if (state.autoRefresh) {
    state.timer = setInterval(() => {
      loadTab(state.tab);
      if (state.tab !== "health") loadHealth().catch(() => {});
    }, 15000);
  }
}

async function init() {
  document.querySelectorAll(".nav-item").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  document.querySelectorAll("#rangePicker button").forEach((b) => b.addEventListener("click", () => {
    state.range = b.dataset.range;
    document.querySelectorAll("#rangePicker button").forEach((x) => x.classList.toggle("active", x === b));
    loadTab(state.tab);
  }));
  $("refreshBtn").addEventListener("click", () => loadTab(state.tab));
  $("autoRefresh").addEventListener("change", (e) => { state.autoRefresh = e.target.checked; scheduleRefresh(); });
  $("logReload").addEventListener("click", loadLogs);
  $("logSearch").addEventListener("keydown", (e) => e.key === "Enter" && loadLogs());
  $("logLevel").addEventListener("change", loadLogs);
  $("userLookupBtn").addEventListener("click", lookupUser);
  $("userLookup").addEventListener("keydown", (e) => e.key === "Enter" && lookupUser());

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

  try {
    META = await api("/api/meta");
    $("cardNames").innerHTML = (META.cards || []).map((c) => `<option value="${esc(c)}">`).join("");
  } catch { /* Meta optional */ }

  await loadHealth().catch(() => {});
  switchTab("health");
  scheduleRefresh();
}

document.addEventListener("DOMContentLoaded", init);
