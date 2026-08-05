/* ===========================================================================
   Kartenbot Web — Oberfläche

   Aufbau: ganz oben Werkzeuge (Anfragen, Formatierung, Meldungen), darunter
   ein Renderer je Bereich. Jeder Renderer bekommt sein Ziel-Element und baut
   seinen Inhalt selbst — es gibt bewusst kein Gerüst von außen, damit ein
   Bereich nie von einem anderen abhängt.
   =========================================================================== */
'use strict';

/* ------------------------------------------------------------- Werkzeuge -- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function num(value) {
  const n = Number(value || 0);
  return n.toLocaleString('de-DE');
}

function dauer(sekunden) {
  if (!sekunden && sekunden !== 0) return '—';
  const s = Math.max(0, Math.floor(sekunden));
  const t = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (t) return `${t} Tage, ${h} Std.`;
  if (h) return `${h} Std., ${m} Min.`;
  return `${m} Min.`;
}

function bytes(value) {
  const einheiten = ['B', 'KB', 'MB', 'GB'];
  let n = Number(value || 0), i = 0;
  while (n >= 1024 && i < einheiten.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(i ? 1 : 0)} ${einheiten[i]}`;
}

function zeitpunkt(wert) {
  if (!wert) return '—';
  const d = typeof wert === 'number' ? new Date(wert * 1000) : new Date(wert);
  if (Number.isNaN(d.getTime())) return String(wert);
  return d.toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
}

/** Zentrale Stelle für alle Anfragen ans Backend.
 *  Fehler kommen als verständlicher deutscher Text zurück, nie als Code. */
async function api(pfad, optionen = {}) {
  const einstellungen = { credentials: 'same-origin', headers: {}, ...optionen };
  if (einstellungen.json !== undefined) {
    einstellungen.method = einstellungen.method || 'POST';
    einstellungen.headers['Content-Type'] = 'application/json';
    einstellungen.body = JSON.stringify(einstellungen.json);
    delete einstellungen.json;
  }
  let antwort;
  try {
    antwort = await fetch(pfad, einstellungen);
  } catch (_) {
    throw new Error('Das Backend ist nicht erreichbar. Läuft der Dienst noch?');
  }
  if (antwort.status === 401) {
    zeigeAnmeldung();
    throw new Error('Die Sitzung ist abgelaufen. Bitte neu anmelden.');
  }
  const text = await antwort.text();
  let daten = null;
  try { daten = text ? JSON.parse(text) : null; } catch (_) { daten = null; }
  if (!antwort.ok) {
    throw new Error((daten && (daten.error || daten.detail)) || `Unerwarteter Fehler (${antwort.status}).`);
  }
  return daten;
}

/* -------------------------------------------------------------- Meldungen -- */
function toast(text, art = '', aktion = null) {
  const box = document.createElement('div');
  box.className = `toast ${art}`;
  box.innerHTML = `<span aria-hidden="true">${art === 'bad' ? '⚠' : art === 'ok' ? '✓' : 'ℹ'}</span>
    <span class="t-main">${esc(text)}</span>`;
  if (aktion) {
    const knopf = document.createElement('button');
    knopf.className = 'undo';
    knopf.textContent = aktion.label;
    knopf.addEventListener('click', () => { box.remove(); aktion.run(); });
    box.appendChild(knopf);
  }
  $('#toasts').appendChild(box);
  setTimeout(() => box.remove(), aktion ? (STATE.undoSekunden * 1000) : 5200);
}

function fehler(e) { toast(e && e.message ? e.message : String(e), 'bad'); }

/* ---------------------------------------------------------------- Dialog -- */
function dialog({ titel, inhalt, knoepfe = [], breit = false }) {
  const modal = $('#modal');
  $('#modalTitle').textContent = titel;
  $('#modalBody').innerHTML = inhalt;
  const fuss = $('#modalFoot');
  fuss.innerHTML = '';
  knoepfe.forEach((k) => {
    const b = document.createElement('button');
    b.className = `btn ${k.art || ''}`;
    b.textContent = k.label;
    b.addEventListener('click', () => k.run ? k.run(schliesseDialog) : schliesseDialog());
    fuss.appendChild(b);
  });
  $('.modal-box').style.width = breit ? 'min(920px, 100%)' : 'min(680px, 100%)';
  modal.hidden = false;
  setTimeout(() => { const f = $('#modalBody input, #modalBody select, #modalBody textarea'); if (f) f.focus(); }, 30);
  return modal;
}
function schliesseDialog() { $('#modal').hidden = true; }

/** Sicherheitsabfrage mit Vorschau: erst zeigen, was passiert, dann fragen. */
function bestaetige({ titel, vorschau, warnung, knopfText = 'Ja, ausführen', gefahr = true }) {
  return new Promise((aufloesen) => {
    dialog({
      titel,
      inhalt: `${warnung ? `<div class="notice warn" style="margin-bottom:16px">${warnung}</div>` : ''}${vorschau}`,
      knoepfe: [
        { label: 'Abbrechen', art: 'ghost', run: (zu) => { zu(); aufloesen(false); } },
        { label: knopfText, art: gefahr ? 'danger' : 'primary', run: (zu) => { zu(); aufloesen(true); } },
      ],
    });
  });
}

/* -------------------------------------------------------------- Zustand -- */
const STATE = {
  tab: 'uebersicht',
  guilds: [],
  guildId: localStorage.getItem('kbweb.guild') || '',
  auth: null,
  undoSekunden: 10,
  jobTimer: null,
  cache: {},
};

const TABS = {
  uebersicht:    { titel: 'Übersicht',            hinweis: 'Zustand des Bots und die wichtigsten Zahlen.' },
  spieler:       { titel: 'Spieler',              hinweis: 'Karten, Infinitydust und Units geben oder nehmen.' },
  karten:        { titel: 'Karten',               hinweis: 'Der komplette Kartenkatalog des Spiels.' },
  statistik:     { titel: 'Statistiken',          hinweis: 'Auswertungen über Kämpfe, Befehle und Einladungen.' },
  rollen:        { titel: 'Rollen & Mitglieder',  hinweis: 'Discord-Rollen vergeben und Mitglieder verwalten.', braucht: 'guild' },
  analyse:       { titel: 'Server-Analyse',       hinweis: 'Chat-Verlauf auswerten und Mitglieder einordnen.',  braucht: 'guild' },
  steuerung:     { titel: 'Bot-Steuerung',        hinweis: 'Alle Schalter des Bots an einer Stelle.' },
  einstellungen: { titel: 'Einstellungen',        hinweis: 'Zugang, KI, Ausführung und Darstellung.' },
};

/* ------------------------------------------------------------- Anmeldung -- */
function zeigeAnmeldung() {
  $('#app').hidden = true;
  $('#gate').hidden = false;
}

async function pruefeAnmeldung() {
  const status = await api('/api/auth/status');
  STATE.auth = status;
  $('#gateTier').textContent = `Zugriff aus: ${status.tier_label}` +
    (status.may_critical ? '' : ' — mächtige Aktionen (Rollen, Kick, Bann) sind hier gesperrt.');

  if (!status.password_configured) {
    $('#gateError').hidden = false;
    $('#gateError').textContent =
      'Es ist kein Passwort gesetzt. Trage WEB_PASSWORD in die .env ein und starte den Dienst neu.';
    $('#gateStepPassword').hidden = true;
    zeigeAnmeldung();
    return false;
  }
  if (status.authenticated) return true;
  if (status.stage === 'pw' && status.discord_configured) {
    $('#gateStepPassword').hidden = true;
    $('#gateStepDiscord').hidden = false;
  }
  zeigeAnmeldung();
  return false;
}

function bindeAnmeldung() {
  $('#gateForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const knopf = $('#gateSubmit');
    knopf.disabled = true;
    knopf.textContent = 'Prüfe …';
    $('#gateError').hidden = true;
    try {
      const antwort = await api('/api/auth/password', { json: { password: $('#gatePassword').value } });
      $('#gatePassword').value = '';
      if (antwort.discord_required) {
        $('#gateStepPassword').hidden = true;
        $('#gateStepDiscord').hidden = false;
        $('#gateSub').textContent = 'Passwort stimmt. Noch ein Schritt.';
      } else {
        await starte();
      }
    } catch (e2) {
      $('#gateError').hidden = false;
      $('#gateError').textContent = e2.message;
    } finally {
      knopf.disabled = false;
      knopf.textContent = 'Anmelden';
    }
  });

  $('#gateDiscord').addEventListener('click', async () => {
    try {
      const { url } = await api('/api/auth/discord/start');
      window.location.href = url;
    } catch (e) {
      $('#gateError').hidden = false;
      $('#gateError').textContent = e.message;
    }
  });

  $('#logout').addEventListener('click', async () => {
    await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
    window.location.reload();
  });
}

/* ---------------------------------------------------------------- Router -- */
function gehZu(tab, options = {}) {
  if (!TABS[tab]) tab = 'uebersicht';
  STATE.tab = tab;
  location.hash = `#/${tab}`;
  $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  $$('.tab').forEach((s) => s.classList.toggle('active', s.dataset.tab === tab));
  $('#pageTitle').textContent = TABS[tab].titel;
  $('#pageHint').textContent = TABS[tab].hinweis;
  $('#guildWrap').hidden = TABS[tab].braucht !== 'guild';
  $('#sidebar').classList.remove('open');
  $('#content').focus({ preventScroll: true });
  zeichne(tab, options);
}

async function zeichne(tab, options = {}) {
  const ziel = $(`.tab[data-tab="${tab}"]`);
  if (!options.leise) {
    ziel.innerHTML = `<div class="panel"><div class="skeleton" style="width:35%"></div>
      <div class="skeleton"></div><div class="skeleton" style="width:70%"></div></div>`;
  }
  try {
    await RENDER[tab](ziel, options);
  } catch (e) {
    ziel.innerHTML = `<div class="notice bad"><strong>Das hat nicht geklappt.</strong><br>${esc(e.message)}
      <div style="margin-top:12px"><button class="btn sm" id="retry">Noch einmal versuchen</button></div></div>`;
    const r = $('#retry', ziel);
    if (r) r.addEventListener('click', () => zeichne(tab));
  }
}

/* ================================ Bereiche ================================ */
const RENDER = {};

/* --------------------------------------------------------------- Übersicht */
RENDER.uebersicht = async (ziel) => {
  const d = await api('/api/overview');
  const z = d.zahlen;
  const logFehler = d.log.fehler_24h || 0;

  ziel.innerHTML = `
    <div class="grid cols-4">
      ${stat(d.online ? 'Bot läuft' : 'Bot offline', d.online ? 'online' : 'offline',
             d.laufzeit_sekunden ? `seit ${dauer(d.laufzeit_sekunden)}` : 'kein Lebenszeichen',
             d.online ? 'good' : 'bad')}
      ${stat('Fehler (24 Std.)', num(logFehler), `${num(d.log.warnungen_24h || 0)} Warnungen`,
             logFehler > 0 ? 'bad' : 'good')}
      ${stat('Spieler', num(z.spieler), `${num(z.karten_gesamt)} Karten insgesamt`)}
      ${stat('Infinitydust', num(z.dust_gesamt), `${num(z.units_gesamt)} Units`)}
    </div>

    <div class="grid cols-2">
      <div class="panel">
        <div class="panel-head"><h2>Zuletzt passiert</h2>
          <p class="muted">Die letzten Ereignisse aus dem Spiel.</p></div>
        ${d.letzte_aktionen.length ? d.letzte_aktionen.slice(0, 12).map((e) => `
          <div class="bar-row">
            <span class="name">${esc(e.event_type)}${e.command_name ? ` · ${esc(e.command_name)}` : ''}</span>
            <span class="val">${zeitpunkt(e.created_at)}</span>
          </div>`).join('') : leer('Noch keine Ereignisse aufgezeichnet.')}
      </div>

      <div class="panel">
        <div class="panel-head"><h2>Technik</h2></div>
        <div class="bar-row"><span class="name">Datenbankgröße</span><span class="val">${bytes(d.db_groesse)}</span></div>
        <div class="bar-row"><span class="name">Aktive Sitzungen</span><span class="val">${num(z.sitzungen)}</span></div>
        <div class="bar-row"><span class="name">Server mit Einstellungen</span><span class="val">${num(z.server)}</span></div>
        <div class="bar-row"><span class="name">Letztes Lebenszeichen</span><span class="val">${zeitpunkt(d.letzter_lebenszeichen)}</span></div>
        ${d.log.letzte_fehler.length ? `
          <h3 style="margin-top:18px">Letzte Fehlermeldungen</h3>
          ${d.log.letzte_fehler.map((f) => `
            <details class="info-row" style="margin-top:8px">
              <summary><span class="tag bad">${esc(f.stufe)}</span>
                <span class="mono">${esc(f.zeit)}</span></summary>
              <div class="why mono">${esc(f.text)}</div>
            </details>`).join('')}` : ''}
      </div>
    </div>`;
};

function stat(label, wert, sub = '', art = '') {
  return `<div class="stat ${art}"><div class="label">${esc(label)}</div>
    <div class="value">${esc(wert)}</div>${sub ? `<div class="sub">${esc(sub)}</div>` : ''}</div>`;
}

function leer(text, icon = '📭') {
  return `<div class="empty"><span class="big" aria-hidden="true">${icon}</span>${esc(text)}</div>`;
}

/* ----------------------------------------------------------------- Spieler */
RENDER.spieler = async (ziel) => {
  const [d, karten] = await Promise.all([api('/api/players'), ladeKarten()]);
  const optionen = karten.map((k) => `<option value="${esc(k.name)}">`).join('');

  ziel.innerHTML = `
    <div class="panel">
      <div class="panel-head"><h2>Spieler nachschlagen</h2>
        <p class="muted">Discord-ID eingeben — du bekommst alles zu dieser Person.</p></div>
      <div class="form-row">
        <label class="field"><span>Discord-ID</span>
          <input id="spSuche" inputmode="numeric" placeholder="z. B. 965593518745731152"></label>
        <div style="display:flex;align-items:flex-end"><button class="btn primary" id="spGo">Anzeigen</button></div>
      </div>
      <div id="spDetail" style="margin-top:20px"></div>
    </div>

    <div class="panel">
      <div class="panel-head"><h2>Geben und nehmen</h2>
        <p class="muted">Wirkt sofort in der Datenbank des Bots — genau wie die Befehle im Discord.</p></div>
      <div class="form-row">
        <label class="field"><span>Discord-ID</span><input id="aktUser" inputmode="numeric"></label>
        <label class="field"><span>Was</span>
          <select id="aktWas">
            <option value="infinitydust">Infinitydust</option>
            <option value="units">Units</option>
            <option value="karte">Karte</option>
            <option value="gruppe">Alle Karten einer Seltenheit</option>
          </select></label>
        <label class="field" id="aktKarteWrap" hidden><span>Karte</span>
          <input id="aktKarte" list="kartenListe" placeholder="Name eingeben">
          <datalist id="kartenListe">${optionen}</datalist></label>
        <label class="field" id="aktGruppeWrap" hidden><span>Seltenheit</span>
          <select id="aktGruppe"></select></label>
        <label class="field" id="aktMengeWrap"><span>Menge</span>
          <input id="aktMenge" type="number" min="1" value="1"></label>
      </div>
      <div class="form-actions">
        <button class="btn primary" id="aktGeben">Geben</button>
        <button class="btn danger" id="aktNehmen">Wegnehmen</button>
      </div>
    </div>

    <div class="grid cols-3">
      ${bestenliste('Meiste Karten', d.top_karten)}
      ${bestenliste('Meiste Infinitydust', d.top_dust)}
      ${bestenliste('Meiste Units', d.top_units)}
    </div>`;

  // Seltenheiten füllen
  const seltenheiten = [...new Set(karten.map((k) => k.seltenheit).filter(Boolean))];
  $('#aktGruppe', ziel).innerHTML = seltenheiten.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join('');

  const wasFeld = $('#aktWas', ziel);
  const passeAn = () => {
    const wert = wasFeld.value;
    $('#aktKarteWrap', ziel).hidden = wert !== 'karte';
    $('#aktGruppeWrap', ziel).hidden = wert !== 'gruppe';
    $('#aktMengeWrap', ziel).hidden = wert === 'gruppe';
  };
  wasFeld.addEventListener('change', passeAn);
  passeAn();

  const zeigeSpieler = async () => {
    const id = $('#spSuche', ziel).value.trim();
    if (!id) return;
    const box = $('#spDetail', ziel);
    box.innerHTML = '<div class="skeleton"></div><div class="skeleton" style="width:60%"></div>';
    try {
      box.innerHTML = spielerKarte(await api(`/api/player/${encodeURIComponent(id)}`));
    } catch (e) {
      box.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
    }
  };
  $('#spGo', ziel).addEventListener('click', zeigeSpieler);
  $('#spSuche', ziel).addEventListener('keydown', (e) => { if (e.key === 'Enter') zeigeSpieler(); });

  const fuehreAus = async (entfernen) => {
    const user = $('#aktUser', ziel).value.trim();
    const was = wasFeld.value;
    const menge = parseInt($('#aktMenge', ziel).value, 10) || 1;
    if (!user) return toast('Bitte zuerst eine Discord-ID eingeben.', 'bad');

    let pfad, koerper, beschreibung;
    if (was === 'karte') {
      const karte = $('#aktKarte', ziel).value.trim();
      if (!karte) return toast('Bitte eine Karte auswählen.', 'bad');
      pfad = '/api/actions/card';
      koerper = { user_id: user, card_name: karte, amount: menge, remove: entfernen };
      beschreibung = `${menge}× „${karte}“`;
    } else if (was === 'gruppe') {
      const seltenheit = $('#aktGruppe', ziel).value;
      pfad = '/api/actions/rarity-group';
      koerper = { user_id: user, rarity: seltenheit, remove: entfernen };
      beschreibung = `alle Karten der Seltenheit „${seltenheit}“`;
    } else {
      pfad = '/api/actions/currency';
      koerper = { currency: was, user_id: user, amount: menge, remove: entfernen };
      beschreibung = `${num(menge)} ${was === 'units' ? 'Units' : 'Infinitydust'}`;
    }

    const ok = await bestaetige({
      titel: entfernen ? 'Wirklich wegnehmen?' : 'Wirklich geben?',
      vorschau: `<p>Das passiert gleich:</p>
        <div class="notice" style="margin-top:12px">
          <strong>${esc(beschreibung)}</strong><br>
          ${entfernen ? 'wird abgezogen von' : 'geht an'} <span class="mono">${esc(user)}</span>
        </div>`,
      gefahr: entfernen,
      knopfText: entfernen ? 'Ja, wegnehmen' : 'Ja, geben',
    });
    if (!ok) return;

    try {
      const antwort = await api(pfad, { json: koerper });
      const rueck = { ...koerper, remove: !entfernen };
      toast(erfolgstext(was, antwort, entfernen), 'ok', {
        label: 'Rückgängig',
        run: async () => {
          try {
            await api(pfad, { json: rueck });
            toast('Zurückgenommen.', 'ok');
            zeigeSpieler();
          } catch (e) { fehler(e); }
        },
      });
      zeigeSpieler();
    } catch (e) { fehler(e); }
  };
  $('#aktGeben', ziel).addEventListener('click', () => fuehreAus(false));
  $('#aktNehmen', ziel).addEventListener('click', () => fuehreAus(true));
};

function erfolgstext(was, antwort, entfernen) {
  if (was === 'gruppe') return `${antwort.karten} Karten ${entfernen ? 'entfernt' : 'vergeben'}.`;
  if (was === 'karte') return `${antwort.gebucht}× „${antwort.karte}“ ${entfernen ? 'entfernt' : 'vergeben'}.`;
  return `${num(antwort.gebucht)} ${antwort.waehrung} ${entfernen ? 'abgezogen' : 'gutgeschrieben'}` +
    ` (neuer Stand: ${num(antwort.nachher)}).`;
}

function bestenliste(titel, zeilen) {
  const max = Math.max(1, ...zeilen.map((z) => Number(z.wert) || 0));
  return `<div class="panel"><div class="panel-head"><h3>${esc(titel)}</h3></div>
    ${zeilen.length ? zeilen.slice(0, 12).map((z) => `
      <div class="bar-row">
        <span class="name mono">${esc(z.user_id)}</span>
        <span class="val">${num(z.wert)}</span>
        <span class="track"><span class="fill" style="width:${(Number(z.wert) || 0) / max * 100}%"></span></span>
      </div>`).join('') : leer('Noch keine Daten.')}
  </div>`;
}

function spielerKarte(p) {
  const tags = (p.profile || []).flatMap((x) => x.tags || []);
  return `
    <div class="grid cols-4">
      ${stat('Karten', num(p.karten_gesamt), `${num(p.karten_verschieden)} verschiedene`)}
      ${stat('Infinitydust', num(p.infinitydust))}
      ${stat('Units', num(p.units))}
      ${stat('Ereignisse', num((p.ereignisse || []).length), 'zuletzt aufgezeichnet')}
    </div>
    ${tags.length ? `<div class="tags" style="margin-top:16px">
      ${tags.map((t) => `<span class="tag accent" title="${esc(t.grund || '')}">${esc(t.label || t)}</span>`).join('')}
    </div>` : ''}
    ${p.karten.length ? `<div class="table-wrap" style="margin-top:16px">
      <table><thead><tr><th>Karte</th><th class="num">Anzahl</th></tr></thead><tbody>
      ${p.karten.map((k) => `<tr><td>${esc(k.karten_name)}</td><td class="num">${num(k.anzahl)}</td></tr>`).join('')}
      </tbody></table></div>` : `<div style="margin-top:16px">${leer('Diese Person besitzt noch keine Karten.')}</div>`}
    ${(p.moderation || []).length ? `<h3 style="margin-top:20px">Moderationsverlauf</h3>
      ${p.moderation.map((m) => `<div class="bar-row"><span class="name">${esc(m.kind)}</span>
        <span class="val">${zeitpunkt(m.created_at)}</span></div>`).join('')}` : ''}`;
}

/* ------------------------------------------------------------------ Karten */
let _kartenCache = null;
async function ladeKarten() {
  if (_kartenCache) return _kartenCache;
  const d = await api('/api/cards');
  _kartenCache = d.karten || [];
  return _kartenCache;
}

RENDER.karten = async (ziel) => {
  const karten = await ladeKarten();
  const seltenheiten = [...new Set(karten.map((k) => k.seltenheit).filter(Boolean))];

  ziel.innerHTML = `
    <div class="panel">
      <div class="panel-head"><h2>Kartenkatalog</h2>
        <p class="muted">${num(karten.length)} Karten aus dem Spiel. Diese Liste kommt direkt aus
          dem Bot — was hier nicht steht, lässt sich auch nicht vergeben.</p></div>
      <div class="form-row">
        <label class="field"><span>Suchen</span><input id="kSuche" placeholder="Name oder Beschreibung"></label>
        <label class="field"><span>Seltenheit</span>
          <select id="kSeltenheit"><option value="">alle</option>
            ${seltenheiten.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join('')}
          </select></label>
      </div>
      <p class="muted" id="kAnzahl" style="margin-top:12px"></p>
      <div id="kListe" style="margin-top:8px"></div>
    </div>`;

  const zeichneListe = () => {
    const suche = $('#kSuche', ziel).value.trim().toLowerCase();
    const seltenheit = $('#kSeltenheit', ziel).value;
    const treffer = karten.filter((k) => {
      if (seltenheit && k.seltenheit !== seltenheit) return false;
      if (!suche) return true;
      return `${k.name} ${k.beschreibung || ''}`.toLowerCase().includes(suche);
    });
    $('#kAnzahl', ziel).textContent = `${num(treffer.length)} von ${num(karten.length)} Karten`;
    $('#kListe', ziel).innerHTML = treffer.length ? treffer.map((k) => `
      <details class="info-row">
        <summary>
          <strong>${esc(k.name)}</strong>
          <span class="tag">${esc(k.seltenheit || '?')}</span>
          <span class="tag">${num(k.hp)} HP</span>
          ${k.varianten.length ? `<span class="tag accent">${k.varianten.length} Varianten</span>` : ''}
        </summary>
        <div class="why">
          ${k.beschreibung ? `${esc(k.beschreibung)}<br><br>` : ''}
          <strong>Angriffe:</strong>
          ${k.angriffe.length ? `<ul style="margin:6px 0 0;padding-left:18px">
            ${k.angriffe.map((a) => `<li>${esc(a.name)}${a.schaden ? ` — ${esc(a.schaden)}` : ''}</li>`).join('')}
          </ul>` : ' keine hinterlegt'}
          ${k.varianten.length ? `<br><strong>Varianten:</strong> ${k.varianten.map((v) =>
            `${esc(v.name)}${v.nur_admin ? ' (nur über Vergabe)' : ''}`).join(', ')}` : ''}
        </div>
      </details>`).join('') : leer('Keine Karte passt zu dieser Suche.', '🔍');
  };
  $('#kSuche', ziel).addEventListener('input', zeichneListe);
  $('#kSeltenheit', ziel).addEventListener('change', zeichneListe);
  zeichneListe();
};

/* -------------------------------------------------------------- Statistik */
RENDER.statistik = async (ziel, options = {}) => {
  const zeitraum = options.zeitraum || '30d';
  const d = await api(`/api/statistics?range=${zeitraum}`);
  const bereiche = { today: 'Heute', '7d': '7 Tage', '30d': '30 Tage', '90d': '90 Tage', all: 'Gesamt' };

  ziel.innerHTML = `
    <div class="panel">
      <div class="panel-head"><h2>Zeitraum</h2>
        <div class="spacer"></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${Object.entries(bereiche).map(([k, l]) =>
            `<button class="btn sm ${k === zeitraum ? 'primary' : 'ghost'}" data-zeit="${k}">${l}</button>`).join('')}
        </div>
      </div>
      <p class="muted">${num(d.ereignisse_gesamt)} Ereignisse ausgewertet.</p>
    </div>

    <div class="grid cols-2">
      ${liste('Meistgenutzte Befehle', d.top_befehle, 'name', 'count', 'Noch keine Befehle aufgezeichnet.')}
      ${liste('Beliebteste Helden', d.top_helden, 'name', 'count')}
      ${liste('Häufigste Angriffe', d.top_angriffe, 'name', 'count')}
      ${liste('Arten von Ereignissen', d.ereignistypen, 'name', 'count')}
      <div class="panel">
        <div class="panel-head"><h3>Siegquote je Held</h3>
          <p class="muted">Nur Helden mit mindestens 3 Kämpfen.</p></div>
        ${d.siegquote.length ? d.siegquote.map((s) => `
          <div class="bar-row">
            <span class="name">${esc(s.held)}</span>
            <span class="val">${s.quote} % (${s.siege}/${s.kaempfe})</span>
            <span class="track"><span class="fill" style="width:${s.quote}%"></span></span>
          </div>`).join('') : leer('Noch keine Kämpfe ausgewertet.')}
      </div>
      ${liste('Meistbesessene Karten', d.top_karten, 'karten_name', 'anzahl')}
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Aktivität nach Uhrzeit</h3></div>
      ${d.pro_stunde.some((s) => s.anzahl) ? balken(d.pro_stunde, 'stunde', 'anzahl')
        : leer('Noch keine Aktivität aufgezeichnet.')}
    </div>`;

  $$('[data-zeit]', ziel).forEach((b) =>
    b.addEventListener('click', () => zeichne('statistik', { zeitraum: b.dataset.zeit })));
};

function liste(titel, zeilen, schluessel, wertSchluessel, leerText = 'Noch keine Daten.') {
  const max = Math.max(1, ...zeilen.map((z) => Number(z[wertSchluessel]) || 0));
  return `<div class="panel"><div class="panel-head"><h3>${esc(titel)}</h3></div>
    ${zeilen.length ? zeilen.slice(0, 12).map((z) => `
      <div class="bar-row">
        <span class="name">${esc(z[schluessel])}</span>
        <span class="val">${num(z[wertSchluessel])}</span>
        <span class="track"><span class="fill" style="width:${(Number(z[wertSchluessel]) || 0) / max * 100}%"></span></span>
      </div>`).join('') : leer(leerText)}
  </div>`;
}

function balken(zeilen, schluessel, wertSchluessel) {
  const max = Math.max(1, ...zeilen.map((z) => z[wertSchluessel]));
  return `<div style="display:flex;align-items:flex-end;gap:3px;height:150px;overflow-x:auto">
    ${zeilen.map((z) => `
      <div style="flex:1;min-width:16px;display:flex;flex-direction:column;align-items:center;gap:5px"
           title="${esc(z[schluessel])}: ${num(z[wertSchluessel])}">
        <div style="width:100%;height:${(z[wertSchluessel] / max) * 115}px;border-radius:4px 4px 0 0;
             background:linear-gradient(180deg,var(--accent),var(--accent-2))"></div>
        <span class="muted" style="font-size:.66rem">${esc(String(z[schluessel]).slice(0, 2))}</span>
      </div>`).join('')}
  </div>`;
}

/* -------------------------------------------------------- Rollen (Bereich) */
RENDER.rollen = async (ziel) => {
  if (!STATE.guildId) { ziel.innerHTML = keinServerHinweis(); return; }
  const gid = STATE.guildId;
  const [rollenInfo, mitgliederInfo] = await Promise.all([
    api(`/api/discord/${gid}/roles`),
    api(`/api/discord/${gid}/members?limit=2000`),
  ]);
  const mitglieder = mitgliederInfo.mitglieder || [];
  const rollen = rollenInfo.roles || [];
  const vergebbar = rollen.filter((r) => r.manageable);

  ziel.innerHTML = `
    ${!rollenInfo.bot_may_manage_roles ? `<div class="notice bad" style="margin-bottom:20px">
      <strong>Dem Bot fehlt das Recht „Rollen verwalten“ auf diesem Server.</strong><br>
      Solange das so ist, kann hier keine Rolle vergeben werden. Du kannst es in den
      Servereinstellungen von Discord bei der Rolle des Bots ergänzen.</div>` : ''}

    <div class="panel">
      <div class="panel-head"><h2>Rollen vergeben</h2>
        <p class="muted">${vergebbar.length} von ${rollen.length} Rollen darf der Bot setzen.
          Grau bedeutet: Discord lässt es nicht zu — der Grund steht dabei.</p></div>

      <div class="grid cols-2">
        <div>
          <h3>1. Wer</h3>
          <label class="field" style="margin:10px 0">
            <span>Mitglieder suchen</span>
            <input id="mSuche" placeholder="Name oder ID">
          </label>
          <div class="pick-list" id="mListe"></div>
          <p class="muted" style="margin-top:8px;font-size:.82rem">
            Oder alle Mitglieder einer bestehenden Rolle:
          </p>
          <select id="vonRolle" style="width:100%;margin-top:6px" class="field">
            <option value="">— keine Übernahme —</option>
            ${rollen.map((r) => `<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('')}
          </select>
        </div>

        <div>
          <h3>2. Welche Rolle</h3>
          <label class="field" style="margin:10px 0">
            <span>Rollen suchen</span><input id="rSuche" placeholder="Rollenname">
          </label>
          <div class="pick-list" id="rListe"></div>
        </div>
      </div>

      <h3 style="margin-top:20px">3. Was soll passieren</h3>
      <div class="form-row" style="margin-top:10px">
        <label class="field"><span>Aktion</span>
          <select id="rAktion"><option value="add">Rolle geben</option>
            <option value="remove">Rolle wegnehmen</option></select></label>
        <label class="field"><span>Läuft ab nach (Minuten, optional)</span>
          <input id="rAblauf" type="number" min="1" placeholder="leer = dauerhaft"></label>
        <label class="field"><span>Grund (steht im Discord-Protokoll)</span>
          <input id="rGrund" value="Über Kartenbot Web"></label>
      </div>
      <div class="form-actions">
        <button class="btn" id="rProbe">Trockenlauf — nur zeigen</button>
        <button class="btn primary" id="rLos">Ausführen</button>
      </div>
      <div id="rErgebnis" style="margin-top:16px"></div>
    </div>

    <div class="grid cols-2">
      <div class="panel">
        <div class="panel-head"><h3>Rangordnung</h3>
          <p class="muted">Der Bot steht auf Platz ${rollenInfo.bot_top_position}.</p></div>
        <div class="pick-list">
          ${rollen.map((r) => `
            <div class="pick ${r.manageable ? '' : 'blocked'}" title="${esc(r.blocked_reason || 'Der Bot darf diese Rolle vergeben.')}">
              <span class="role-dot" style="background:#${(r.color || 0).toString(16).padStart(6, '0')}"></span>
              <span class="pick-main"><span class="pick-name">${esc(r.name)}</span>
                <span class="pick-sub">${r.manageable ? 'vergebbar' : esc(r.blocked_reason || '')}</span></span>
            </div>`).join('')}
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h3>Rechte einer Person prüfen</h3>
          <p class="muted">Was darf jemand auf diesem Server wirklich?</p></div>
        <div class="form-row">
          <label class="field"><span>Discord-ID</span><input id="permId" inputmode="numeric"></label>
          <div style="display:flex;align-items:flex-end"><button class="btn" id="permGo">Prüfen</button></div>
        </div>
        <div id="permOut" style="margin-top:14px"></div>
      </div>
    </div>

    <div class="panel" id="verlaufPanel">
      <div class="panel-head"><h3>Rollen-Verlauf</h3>
        <p class="muted">Wer hat wann welche Rolle bekommen — auch von der Website aus.</p></div>
      <div id="verlaufListe"><div class="skeleton"></div></div>
    </div>`;

  // --- Mitglieder- und Rollenauswahl ---
  const zeichneMitglieder = () => {
    const suche = $('#mSuche', ziel).value.trim().toLowerCase();
    const treffer = mitglieder.filter((m) => {
      if (!suche) return true;
      const u = m.user || {};
      return `${u.username || ''} ${u.global_name || ''} ${m.nick || ''} ${u.id}`.toLowerCase().includes(suche);
    }).slice(0, 300);
    $('#mListe', ziel).innerHTML = treffer.length ? treffer.map((m) => {
      const u = m.user || {};
      const name = m.nick || u.global_name || u.username || u.id;
      return `<label class="pick"><input type="checkbox" class="mPick" value="${esc(u.id)}">
        <span class="pick-main"><span class="pick-name">${esc(name)}</span>
        <span class="pick-sub mono">${esc(u.id)}</span></span></label>`;
    }).join('') : `<p class="muted" style="padding:12px">Niemand gefunden.</p>`;
  };
  const zeichneRollen = () => {
    const suche = $('#rSuche', ziel).value.trim().toLowerCase();
    const treffer = rollen.filter((r) => !suche || r.name.toLowerCase().includes(suche));
    $('#rListe', ziel).innerHTML = treffer.map((r) => `
      <label class="pick ${r.manageable ? '' : 'blocked'}" title="${esc(r.blocked_reason || '')}">
        <input type="checkbox" class="rPick" value="${esc(r.id)}" ${r.manageable ? '' : 'disabled'}>
        <span class="role-dot" style="background:#${(r.color || 0).toString(16).padStart(6, '0')}"></span>
        <span class="pick-main"><span class="pick-name">${esc(r.name)}</span>
          ${r.manageable ? '' : `<span class="pick-sub">${esc(r.blocked_reason || '')}</span>`}</span>
      </label>`).join('');
  };
  $('#mSuche', ziel).addEventListener('input', zeichneMitglieder);
  $('#rSuche', ziel).addEventListener('input', zeichneRollen);
  zeichneMitglieder();
  zeichneRollen();

  const sammle = () => ({
    guild_id: gid,
    user_ids: $$('.mPick:checked', ziel).map((c) => c.value),
    role_ids: $$('.rPick:checked', ziel).map((c) => c.value),
    action: $('#rAktion', ziel).value,
    reason: $('#rGrund', ziel).value || 'Über Kartenbot Web',
    from_role_id: $('#vonRolle', ziel).value || null,
    expires_in_minutes: parseInt($('#rAblauf', ziel).value, 10) || null,
  });

  const zeigeErgebnis = (d) => {
    const z = d.zusammenfassung;
    $('#rErgebnis', ziel).innerHTML = `
      <div class="notice ${d.trockenlauf ? '' : 'ok'}">
        <strong>${d.trockenlauf ? 'Vorschau — es wurde nichts verändert.' : 'Ausgeführt.'}</strong><br>
        ${z.aenderungen} Änderung(en) bei ${z.mitglieder} Mitglied(ern) und ${z.rollen} Rolle(n).
        ${z.ohne_wirkung ? `<br>${z.ohne_wirkung} ohne Wirkung (schon im Zielzustand).` : ''}
        ${d.auftrag ? `<br>Auftrag ${d.auftrag.id} läuft — der Fortschritt erscheint gleich.` : ''}
        ${d.hinweis ? `<br>${esc(d.hinweis)}` : ''}
      </div>
      ${z.abgelehnte_rollen.length ? `<div class="notice warn" style="margin-top:10px">
        <strong>Abgelehnte Rollen:</strong><br>
        ${z.abgelehnte_rollen.map((r) => `• ${esc(r.name || r.role_id)}: ${esc(r.grund)}`).join('<br>')}
      </div>` : ''}
      ${d.vorschau && d.vorschau.length ? `<div class="table-wrap" style="margin-top:12px">
        <table><thead><tr><th>Mitglied</th><th>Rolle</th><th>Ergebnis</th></tr></thead><tbody>
        ${d.vorschau.slice(0, 200).map((v) => `<tr><td>${esc(v.name)}</td><td>${esc(v.role_name)}</td>
          <td>${zustandsText(v.zustand)}</td></tr>`).join('')}
        </tbody></table></div>` : ''}`;
    if (d.auftrag) beobachteAuftrag(d.auftrag.id);
  };

  $('#rProbe', ziel).addEventListener('click', async () => {
    try { zeigeErgebnis(await api('/api/roles/apply', { json: { ...sammle(), dry_run: true } })); }
    catch (e) { fehler(e); }
  });

  $('#rLos', ziel).addEventListener('click', async () => {
    const daten = sammle();
    if (!daten.role_ids.length) return toast('Bitte mindestens eine Rolle auswählen.', 'bad');
    if (!daten.user_ids.length && !daten.from_role_id) {
      return toast('Bitte Mitglieder auswählen oder eine Rolle zum Übernehmen wählen.', 'bad');
    }
    let probe;
    try { probe = await api('/api/roles/apply', { json: { ...daten, dry_run: true } }); }
    catch (e) { return fehler(e); }

    const z = probe.zusammenfassung;
    if (!z.aenderungen) { zeigeErgebnis(probe); return toast('Es gäbe nichts zu tun.', ''); }

    const ok = await bestaetige({
      titel: daten.action === 'add' ? 'Rollen wirklich vergeben?' : 'Rollen wirklich entziehen?',
      warnung: 'Rollen können Rechte auf dem Server verändern. Bitte kurz prüfen.',
      vorschau: `<div class="notice"><strong>${z.aenderungen} Änderung(en)</strong> bei
        ${z.mitglieder} Mitglied(ern).${z.ohne_wirkung ? ` ${z.ohne_wirkung} bleiben unberührt.` : ''}</div>
        <div class="table-wrap" style="margin-top:12px;max-height:280px;overflow-y:auto">
        <table><thead><tr><th>Mitglied</th><th>Rolle</th><th>Ergebnis</th></tr></thead><tbody>
        ${probe.vorschau.filter((v) => v.zustand.startsWith('wird')).slice(0, 100)
          .map((v) => `<tr><td>${esc(v.name)}</td><td>${esc(v.role_name)}</td>
            <td>${zustandsText(v.zustand)}</td></tr>`).join('')}
        </tbody></table></div>`,
      knopfText: 'Ja, ausführen',
    });
    if (!ok) return;

    try {
      zeigeErgebnis(await api('/api/roles/apply', { json: daten }));
      ladeVerlauf();
    } catch (e) { fehler(e); }
  });

  $('#permGo', ziel).addEventListener('click', async () => {
    const id = $('#permId', ziel).value.trim();
    if (!id) return;
    const box = $('#permOut', ziel);
    box.innerHTML = '<div class="skeleton"></div>';
    try {
      const p = await api(`/api/discord/${gid}/permissions/${encodeURIComponent(id)}`);
      box.innerHTML = `
        <p><strong>${esc(p.name)}</strong>${p.ist_administrator ? ' <span class="tag warn">Administrator</span>' : ''}</p>
        <p class="muted" style="font-size:.84rem">Beigetreten: ${zeitpunkt(p.beigetreten_am)}
          ${p.in_auszeit_bis ? ` · in Auszeit bis ${zeitpunkt(p.in_auszeit_bis)}` : ''}</p>
        <div class="tags" style="margin:12px 0">${p.rollen.map((r) => `<span class="tag">${esc(r.name)}</span>`).join('')}</div>
        <div class="tags">${p.rechte.filter((r) => r.aktiv).map((r) =>
          `<span class="tag ok" title="${esc(r.grund || '')}">${esc(r.name)}</span>`).join('')}</div>`;
    } catch (e) { box.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
  });

  const ladeVerlauf = async () => {
    try {
      const d = await api(`/api/roles/${gid}/history?limit=60`);
      $('#verlaufListe', ziel).innerHTML = d.verlauf.length ? d.verlauf.map((v) => `
        <div class="bar-row"><span class="name">
          <span class="mono">${esc(v.user_id)}</span> ·
          ${v.action === 'add' ? 'bekam' : 'verlor'} Rolle <span class="mono">${esc(v.role_id)}</span>
          ${v.actor ? ` · durch ${esc(v.actor)}` : ''}</span>
          <span class="val">${zeitpunkt(v.created_at)}</span></div>`).join('')
        : leer('Über diese Website wurde hier noch keine Rolle vergeben.');
    } catch (e) { $('#verlaufListe', ziel).innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
  };
  ladeVerlauf();
};

/** Ehrlicher Hinweis: unterscheidet „nichts ausgewählt" von „gar nichts da". */
function keinServerHinweis() {
  if (STATE.guilds.length) {
    return `<div class="notice warn">Bitte wähle oben rechts einen Server aus.</div>`;
  }
  return `<div class="notice warn">
    <strong>Es ist kein Discord-Server erreichbar.</strong><br>
    Dieser Bereich spricht direkt mit Discord und braucht dafür den Bot-Token.
    Trage <span class="mono">BOT_TOKEN</span> in die Datei <span class="mono">web/.env</span> ein
    und starte den Dienst neu. Alle anderen Bereiche funktionieren auch ohne.
  </div>`;
}

function zustandsText(zustand) {
  return {
    wird_gesetzt: '<span class="tag ok">wird gesetzt</span>',
    wird_entfernt: '<span class="tag warn">wird entfernt</span>',
    hatte_schon: '<span class="tag">hat sie schon</span>',
    hatte_nicht: '<span class="tag">hat sie gar nicht</span>',
  }[zustand] || esc(zustand);
}

/* ----------------------------------------------------------------- Analyse */
const SCAN_ZEITRAEUME = {
  none: 'gar nichts', '3m': 'letzte 3 Monate', '6m': 'letzte 6 Monate',
  '1y': 'letztes Jahr', all: 'alles',
};

RENDER.analyse = async (ziel) => {
  if (!STATE.guildId) { ziel.innerHTML = keinServerHinweis(); return; }
  const gid = STATE.guildId;
  const [kanalInfo, laeufe, profile] = await Promise.all([
    api(`/api/discord/${gid}/channels`),
    api(`/api/scan/${gid}/runs`),
    api(`/api/scan/${gid}/profiles?limit=1000`),
  ]);
  const kanaele = kanalInfo.kanaele || [];

  ziel.innerHTML = `
    <div class="panel">
      <div class="panel-head"><h2>Verlauf auswerten</h2>
        <p class="muted">Der Bot liest die gewählten Kanäle und leitet daraus ab, wer wie unterwegs ist.
          <strong>Nachrichtentexte werden dabei nie gespeichert</strong> — nur Zahlen und Einordnungen.</p></div>

      <div class="grid cols-2">
        <div>
          <h3>Kanäle</h3>
          <div style="display:flex;gap:8px;margin:10px 0">
            <button class="btn sm ghost" id="kAlle">Alle</button>
            <button class="btn sm ghost" id="kKeine">Keine</button>
          </div>
          <div class="pick-list">
            ${kanaele.map((c) => `<label class="pick">
              <input type="checkbox" class="cPick" value="${esc(c.id)}">
              <span class="pick-main"><span class="pick-name">#${esc(c.name)}</span></span></label>`).join('')}
          </div>
        </div>
        <div>
          <h3>Zeitraum</h3>
          <div style="display:flex;flex-direction:column;gap:8px;margin-top:10px">
            ${Object.entries(SCAN_ZEITRAEUME).map(([k, l]) => `
              <label class="pick"><input type="radio" name="zeitraum" value="${k}"
                ${k === '3m' ? 'checked' : ''}>
                <span class="pick-main"><span class="pick-name">${l}</span></span></label>`).join('')}
          </div>
          <label class="pick" style="margin-top:14px">
            <input type="checkbox" id="kiAn">
            <span class="pick-main"><span class="pick-name">Zusätzlich mit KI einschätzen</span>
              <span class="pick-sub">Nur wirksam, wenn die KI in den Einstellungen erlaubt ist.</span></span>
          </label>
          <div class="form-actions">
            <button class="btn primary" id="scanLos">Auswertung starten</button>
          </div>
        </div>
      </div>
      <div id="scanStatus" style="margin-top:16px"></div>
    </div>

    <div class="panel">
      <div class="panel-head"><h2>Ergebnisse</h2>
        <p class="muted">${num(profile.profile.length)} Mitglieder ausgewertet.</p>
        <div class="spacer"></div>
        <input id="pSuche" placeholder="Name oder Einordnung suchen" style="max-width:240px"
               class="field">
      </div>
      <div id="pListe"></div>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Frühere Läufe</h3></div>
      ${laeufe.laeufe.length ? laeufe.laeufe.map((l) => `
        <details class="info-row">
          <summary><span class="tag ${l.status === 'done' ? 'ok' : l.status === 'failed' ? 'bad' : 'warn'}">${esc(l.status)}</span>
            <span>${esc(SCAN_ZEITRAEUME[l.range_key] || l.range_key || '')}</span>
            <span class="muted">${zeitpunkt(l.started_at)}</span></summary>
          <div class="why">
            ${num(l.messages_seen)} Nachrichten, ${num(l.members_seen)} Mitglieder.
            ${l.error ? `<br><strong>Fehler:</strong> ${esc(l.error)}` : ''}
            ${(l.summary && l.summary.uebersprungene_kanaele || []).length ?
              `<br>Übersprungen: ${l.summary.uebersprungene_kanaele.map((u) =>
                `#${esc(u.name || u.channel_id)} (${esc(u.grund)})`).join(', ')}` : ''}
          </div>
        </details>`).join('') : leer('Für diesen Server wurde noch nie eine Auswertung gemacht.', '🔬')}
    </div>`;

  $('#kAlle', ziel).addEventListener('click', () => $$('.cPick', ziel).forEach((c) => { c.checked = true; }));
  $('#kKeine', ziel).addEventListener('click', () => $$('.cPick', ziel).forEach((c) => { c.checked = false; }));

  const zeichneProfile = () => {
    const suche = $('#pSuche', ziel).value.trim().toLowerCase();
    const treffer = profile.profile.filter((p) => {
      if (!suche) return true;
      const tags = (p.tags || []).map((t) => t.label || t).join(' ');
      return `${p.stats?.name || ''} ${p.user_id} ${tags}`.toLowerCase().includes(suche);
    });
    $('#pListe', ziel).innerHTML = treffer.length ? treffer.slice(0, 300).map((p) => {
      const s = p.stats || {};
      return `<details class="info-row">
        <summary><strong>${esc(s.name || p.user_id)}</strong>
          ${(p.tags || []).map((t) => `<span class="tag accent">${esc(t.label || t)}</span>`).join('')}
          <span class="muted" style="margin-left:auto">${num(s.nachrichten || 0)} Nachr.</span></summary>
        <div class="why">
          ${(p.tags || []).map((t) => `<div style="margin-bottom:6px"><strong>${esc(t.label || t)}:</strong>
            ${esc(t.grund || '')}</div>`).join('') || 'Keine Auffälligkeiten.'}
          <div style="margin-top:10px" class="mono">
            Ø ${s.laenge_schnitt || 0} Zeichen · Bilder ${s.bild_anteil || 0} % ·
            GIFs ${s.gif_anteil || 0} % · nachts ${s.nacht_anteil || 0} % ·
            Reaktionen ${s.reaktionen_pro_nachricht || 0}/Nachricht · Kanäle ${s.kanaele || 0}
          </div>
          ${p.ai_summary ? `<div class="notice" style="margin-top:10px">${esc(p.ai_summary)}</div>` : ''}
        </div>
      </details>`;
    }).join('') : leer('Noch keine Auswertung vorhanden — starte oben eine.', '🔬');
  };
  $('#pSuche', ziel).addEventListener('input', zeichneProfile);
  zeichneProfile();

  $('#scanLos', ziel).addEventListener('click', async () => {
    const kanalIds = $$('.cPick:checked', ziel).map((c) => c.value);
    const zeitraum = ($('input[name="zeitraum"]:checked', ziel) || {}).value || '3m';
    if (zeitraum === 'none') return toast('Zeitraum steht auf „gar nichts“ — es gibt nichts zu tun.', '');
    if (!kanalIds.length) return toast('Bitte mindestens einen Kanal auswählen.', 'bad');

    const ok = await bestaetige({
      titel: 'Auswertung starten?',
      gefahr: false,
      knopfText: 'Ja, starten',
      vorschau: `<div class="notice">
        <strong>${kanalIds.length} Kanal/Kanäle</strong>, Zeitraum: <strong>${SCAN_ZEITRAEUME[zeitraum]}</strong>.<br><br>
        Der Bot liest die Nachrichten in kleinen Häppchen mit Pausen — er bleibt dabei normal
        bedienbar. Je nach Größe kann das einige Minuten bis Stunden dauern. Du kannst jederzeit
        abbrechen, und du musst die Seite nicht offen lassen.<br><br>
        <strong>Es werden keine Nachrichtentexte gespeichert.</strong></div>`,
    });
    if (!ok) return;

    try {
      const auftrag = await api('/api/scan/start', {
        json: { guild_id: gid, channel_ids: kanalIds, range_key: zeitraum, use_ai: $('#kiAn', ziel).checked },
      });
      if (auftrag.uebersprungen) return toast(auftrag.hinweis, '');
      toast('Auswertung gestartet.', 'ok');
      beobachteAuftrag(auftrag.id, $('#scanStatus', ziel));
    } catch (e) { fehler(e); }
  });

  const laufend = await api(`/api/jobs?guild_id=${gid}`).catch(() => null);
  const offen = laufend && laufend.laufend.find((j) => j.kind === 'scan.history');
  if (offen) beobachteAuftrag(offen.id, $('#scanStatus', ziel));
};

/* Fortschritt eines Auftrags verfolgen. */
function beobachteAuftrag(id, box) {
  const ziel = box || $('#scanStatus') || null;
  if (STATE.jobTimer) clearInterval(STATE.jobTimer);

  const tick = async () => {
    let job;
    try { job = await api(`/api/jobs/${id}`); }
    catch (_) { clearInterval(STATE.jobTimer); return; }

    if (ziel) {
      const fertig = ['done', 'failed', 'cancelled'].includes(job.status);
      ziel.innerHTML = `
        <div class="notice ${job.status === 'failed' ? 'bad' : job.status === 'done' ? 'ok' : ''}">
          <strong>${esc(job.kind_label)}</strong> — ${esc(zustandName(job.status))}
          ${job.stage ? `<br><span class="muted">${esc(job.stage)}</span>` : ''}
          ${job.total ? `<div class="progress" style="margin-top:10px">
            <i style="width:${job.percent || 0}%"></i></div>
            <span class="muted" style="font-size:.8rem">${job.progress} von ${job.total}</span>` : ''}
          ${job.error ? `<br>${esc(job.error)}` : ''}
          ${!fertig ? `<div style="margin-top:10px">
            <button class="btn sm danger" id="jobStop">Abbrechen</button></div>` : ''}
        </div>`;
      const stop = $('#jobStop', ziel);
      if (stop) {
        stop.addEventListener('click', async () => {
          try { await api(`/api/jobs/${id}/cancel`, { method: 'POST' }); toast('Abbruch angefordert.', ''); }
          catch (e) { fehler(e); }
        });
      }
      if (fertig) {
        clearInterval(STATE.jobTimer);
        STATE.jobTimer = null;
        if (job.status === 'done') {
          toast('Auftrag fertig.', 'ok');
          if (STATE.tab === 'analyse') zeichne('analyse', { leise: true });
        }
      }
    }
  };
  tick();
  STATE.jobTimer = setInterval(tick, 2500);
}

function zustandName(status) {
  return { pending: 'wartet', running: 'läuft', done: 'fertig',
           failed: 'fehlgeschlagen', cancelled: 'abgebrochen' }[status] || status;
}

/* --------------------------------------------------------------- Steuerung */
RENDER.steuerung = async (ziel) => {
  const d = await api('/api/guild-settings');
  const server = d.server || [];

  ziel.innerHTML = `
    <div class="panel">
      <div class="panel-head"><h2>Schalter je Server</h2>
        <p class="muted">Wirken sofort — genau wie im Entwicklerpanel des Bots.</p></div>
      ${server.length ? server.map((s) => `
        <details class="info-row" open>
          <summary><strong class="mono">${esc(s.guild_id)}</strong>
            ${s.wartungsmodus ? '<span class="tag warn">Wartung</span>' : ''}
            ${s.alpha ? '<span class="tag accent">Alpha</span>' : ''}
            ${s.beta ? '<span class="tag accent">Beta</span>' : ''}</summary>
          <div class="why">
            <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:6px">
              ${schalter(s.guild_id, 'maintenance_mode', 'Wartungsmodus', s.wartungsmodus)}
              ${schalter(s.guild_id, 'alpha_enabled', 'Alpha-Phase', s.alpha)}
              ${schalter(s.guild_id, 'beta_enabled', 'Beta-Phase', s.beta)}
            </div>
            <p style="margin-top:10px">Freigegebene Kanäle: ${s.erlaubte_kanaele.length
              ? s.erlaubte_kanaele.map((c) => `<span class="tag mono">${esc(c)}</span>`).join(' ')
              : '<em>keine — der Bot antwortet dort überall</em>'}</p>
          </div>
        </details>`).join('') : leer('Für keinen Server sind bisher Einstellungen gespeichert.')}
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Interne Werte des Bots</h3>
        <p class="muted">Zur Kontrolle — hier wird nichts verändert.</p></div>
      <div class="table-wrap"><table><thead><tr><th>Schlüssel</th><th>Wert</th></tr></thead><tbody>
        ${Object.entries(d.bot || {}).map(([k, v]) =>
          `<tr><td class="mono">${esc(k)}</td><td class="mono">${esc(v)}</td></tr>`).join('')}
      </tbody></table></div>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Protokoll der Website</h3>
        <p class="muted">Jede Aktion, die hier ausgelöst wurde.</p></div>
      <div id="auditListe"><div class="skeleton"></div></div>
    </div>`;

  $$('[data-flag]', ziel).forEach((b) => b.addEventListener('click', async () => {
    const { guild, flag, wert } = b.dataset;
    try {
      await api('/api/actions/flag', { json: { guild_id: guild, flag, enabled: wert !== '1' } });
      toast('Schalter geändert.', 'ok');
      zeichne('steuerung');
    } catch (e) { fehler(e); }
  }));

  try {
    const a = await api('/api/audit?limit=60');
    $('#auditListe', ziel).innerHTML = a.eintraege.length ? a.eintraege.map((e) => `
      <div class="bar-row"><span class="name">
        ${e.ok ? '' : '<span class="tag bad">Fehler</span> '}
        <strong>${esc(e.action)}</strong> ${e.target ? `· ${esc(e.target)}` : ''}
        ${e.detail ? `· ${esc(e.detail)}` : ''} <span class="muted">(${esc(e.actor || '?')})</span></span>
      <span class="val">${zeitpunkt(e.created_at)}</span></div>`).join('')
      : leer('Noch keine Aktionen protokolliert.');
  } catch (e) { $('#auditListe', ziel).innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
};

function schalter(guild, flag, label, aktiv) {
  return `<button class="btn sm ${aktiv ? 'primary' : 'ghost'}" data-flag="${esc(flag)}"
    data-guild="${esc(guild)}" data-wert="${aktiv ? '1' : '0'}"
    >${esc(label)}: ${aktiv ? 'an' : 'aus'}</button>`;
}

/* ----------------------------------------------------------- Einstellungen */
RENDER.einstellungen = async (ziel) => {
  const [d, ki] = await Promise.all([
    api('/api/settings'),
    api('/api/ai/status').catch((e) => ({ ok: false, error: e.message })),
  ]);
  const gruppen = {};
  d.einstellungen.forEach((e) => { (gruppen[e.group] = gruppen[e.group] || []).push(e); });

  ziel.innerHTML = `
    <div class="panel">
      <div class="panel-head"><h2>KI-Verbindung</h2></div>
      <div class="notice ${ki.ok ? 'ok' : 'warn'}">
        ${ki.ok ? `Ollama erreichbar unter <span class="mono">${esc(ki.url)}</span> (${esc(ki.version || '')}).
          Modell: <strong>${esc(ki.model || 'noch keines gewählt')}</strong>.`
        : `Ollama ist nicht erreichbar. ${esc(ki.error || '')}`}
      </div>
      <div class="form-actions">
        <button class="btn" id="kiModelle">Modelle anzeigen</button>
        <button class="btn primary" id="kiFinden">Passendes Modell suchen</button>
      </div>
      <div id="kiOut" style="margin-top:14px"></div>
    </div>

    ${Object.entries(gruppen).map(([gruppe, felder]) => `
      <div class="panel">
        <div class="panel-head"><h3>${esc(gruppe)}</h3></div>
        <div style="display:grid;gap:18px">
          ${felder.map((f) => feldHtml(f)).join('')}
        </div>
      </div>`).join('')}

    <div class="form-actions">
      <button class="btn primary" id="setSpeichern">Einstellungen speichern</button>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Zugang zurücksetzen</h3></div>
      <p class="muted">Hebt die Bindung an dein Discord-Konto auf. Der nächste erfolgreiche
        Discord-Login beansprucht den Zugang dann neu. Nur aus dem Heimnetz möglich.</p>
      <div class="form-actions"><button class="btn danger" id="ownerReset">Besitzer zurücksetzen</button></div>
    </div>`;

  $('#setSpeichern', ziel).addEventListener('click', async () => {
    const changes = {};
    $$('[data-key]', ziel).forEach((el) => {
      changes[el.dataset.key] = el.type === 'checkbox' ? (el.checked ? '1' : '0') : el.value;
    });
    try {
      await api('/api/settings', { json: { changes } });
      toast('Gespeichert.', 'ok');
      STATE.undoSekunden = parseInt(changes['ui.confirm_seconds'], 10) || 10;
      wendeDesignAn(changes['ui.theme']);
    } catch (e) { fehler(e); }
  });

  $('#kiModelle', ziel).addEventListener('click', async () => {
    const box = $('#kiOut', ziel);
    box.innerHTML = '<div class="skeleton"></div>';
    try {
      const m = await api('/api/ai/models');
      box.innerHTML = `<div class="table-wrap"><table>
        <thead><tr><th>Modell</th><th>Größe</th><th>Parameter</th></tr></thead><tbody>
        ${m.modelle.map((x) => `<tr><td class="mono">${esc(x.name)}</td>
          <td class="num">${bytes(x.size)}</td><td>${esc(x.parameter_size || '')}</td></tr>`).join('')}
      </tbody></table></div>`;
    } catch (e) { box.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
  });

  $('#kiFinden', ziel).addEventListener('click', async () => {
    const box = $('#kiOut', ziel);
    box.innerHTML = `<div class="notice">Mehrere Modelle werden gleichzeitig mit einer kleinen
      Testaufgabe geprüft. Das kann ein bis zwei Minuten dauern …</div><div class="skeleton"></div>`;
    try {
      const r = await api('/api/ai/find-model', { json: { timeout: 90 } });
      box.innerHTML = `
        ${r.recommended ? `<div class="notice ok">Empfehlung: <strong>${esc(r.recommended)}</strong>
          — schnellstes Modell, das die Testaufgabe richtig löst.
          <button class="btn sm" id="kiUebernehmen" style="margin-left:10px">Übernehmen</button></div>`
        : '<div class="notice warn">Kein Modell hat die Testaufgabe bestanden.</div>'}
        <div class="table-wrap" style="margin-top:12px"><table>
          <thead><tr><th>Modell</th><th>Ergebnis</th><th class="num">Dauer</th><th>Antwort</th></tr></thead><tbody>
          ${r.tested.map((t) => `<tr><td class="mono">${esc(t.model)}</td>
            <td>${t.ok ? '<span class="tag ok">geeignet</span>' : '<span class="tag bad">ungeeignet</span>'}</td>
            <td class="num">${t.seconds ? `${t.seconds} s` : '—'}</td>
            <td class="muted">${esc((t.answer || t.error || '').slice(0, 70))}</td></tr>`).join('')}
        </tbody></table></div>`;
      const uebernehmen = $('#kiUebernehmen', box);
      if (uebernehmen) {
        uebernehmen.addEventListener('click', async () => {
          try {
            await api('/api/settings', { json: { changes: { 'ollama.model': r.recommended } } });
            toast(`Modell „${r.recommended}“ übernommen.`, 'ok');
            zeichne('einstellungen');
          } catch (e) { fehler(e); }
        });
      }
    } catch (e) { box.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
  });

  $('#ownerReset', ziel).addEventListener('click', async () => {
    const ok = await bestaetige({
      titel: 'Besitzer wirklich zurücksetzen?',
      warnung: 'Danach kann sich das nächste Discord-Konto, das sich anmeldet, als Besitzer eintragen. '
        + 'Mach das nur, wenn du das Konto wechseln willst.',
      vorschau: '<p>Die Passwort-Hürde bleibt unverändert bestehen.</p>',
      knopfText: 'Ja, zurücksetzen',
    });
    if (!ok) return;
    try { await api('/api/auth/owner/reset', { method: 'POST' }); toast('Zurückgesetzt.', 'ok'); }
    catch (e) { fehler(e); }
  });
};

function feldHtml(f) {
  const gemeinsam = `data-key="${esc(f.key)}"`;
  let eingabe;
  if (f.type === 'bool') {
    eingabe = `<input type="checkbox" ${gemeinsam} ${f.value === '1' ? 'checked' : ''}>`;
  } else if (f.type === 'choice') {
    eingabe = `<select ${gemeinsam}>${f.choices.map((c) =>
      `<option value="${esc(c)}" ${c === f.value ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>`;
  } else if (f.type === 'textarea') {
    eingabe = `<textarea ${gemeinsam}>${esc(f.value)}</textarea>`;
  } else {
    eingabe = `<input type="${f.type === 'int' ? 'number' : 'text'}" ${gemeinsam} value="${esc(f.value)}">`;
  }
  return `<label class="field"><span>${esc(f.label)}</span>${eingabe}
    <span class="hint">${esc(f.help)}</span></label>`;
}

/* ------------------------------------------------------------ Globale Suche */
function baueSuchEintraege() {
  const eintraege = Object.entries(TABS).map(([key, t]) => ({
    titel: t.titel, unter: t.hinweis, icon: '📁', run: () => gehZu(key),
  }));
  eintraege.push(
    { titel: 'Karte geben', unter: 'Spieler → Geben und nehmen', icon: '🃏', run: () => gehZu('spieler') },
    { titel: 'Infinitydust geben', unter: 'Spieler → Geben und nehmen', icon: '💎', run: () => gehZu('spieler') },
    { titel: 'Units geben', unter: 'Spieler → Geben und nehmen', icon: '🔷', run: () => gehZu('spieler') },
    { titel: 'Rolle vergeben', unter: 'Rollen & Mitglieder', icon: '🛡', run: () => gehZu('rollen') },
    { titel: 'Rechte einer Person prüfen', unter: 'Rollen & Mitglieder', icon: '🔎', run: () => gehZu('rollen') },
    { titel: 'Verlauf auswerten', unter: 'Server-Analyse', icon: '🔬', run: () => gehZu('analyse') },
    { titel: 'Wartungsmodus umschalten', unter: 'Bot-Steuerung', icon: '🎛', run: () => gehZu('steuerung') },
    { titel: 'KI-Modell suchen', unter: 'Einstellungen → KI', icon: '🤖', run: () => gehZu('einstellungen') },
    { titel: 'Design wechseln', unter: 'Einstellungen → Darstellung', icon: '🌓', run: () => gehZu('einstellungen') },
  );
  return eintraege;
}

let _sucheAuswahl = 0;
let _sucheTreffer = [];

function oeffneSuche() {
  const p = $('#palette');
  p.hidden = false;
  $('#paletteInput').value = '';
  sucheAktualisieren('');
  $('#paletteInput').focus();
}
function schliesseSuche() { $('#palette').hidden = true; }

function sucheAktualisieren(text) {
  const alle = baueSuchEintraege();
  const suche = text.trim().toLowerCase();
  _sucheTreffer = suche
    ? alle.filter((e) => `${e.titel} ${e.unter}`.toLowerCase().includes(suche))
    : alle;
  _sucheAuswahl = 0;
  zeichneSuche();

  // Ist die Eingabe eine Discord-ID? Dann direkt anbieten.
  if (/^\d{15,22}$/.test(suche)) {
    _sucheTreffer = [{
      titel: `Spieler ${suche} anzeigen`, unter: 'Direkt nachschlagen', icon: '👤',
      run: () => { gehZu('spieler'); setTimeout(() => {
        const feld = $('.tab[data-tab="spieler"] #spSuche');
        if (feld) { feld.value = suche; $('.tab[data-tab="spieler"] #spGo').click(); }
      }, 250); },
    }, ..._sucheTreffer];
    zeichneSuche();
  }
}

function zeichneSuche() {
  const box = $('#paletteResults');
  box.innerHTML = _sucheTreffer.length ? _sucheTreffer.map((e, i) => `
    <div class="palette-item ${i === _sucheAuswahl ? 'sel' : ''}" data-i="${i}">
      <span aria-hidden="true">${e.icon}</span>
      <span class="pi-main"><span class="pi-title">${esc(e.titel)}</span>
        <span class="pi-sub">${esc(e.unter)}</span></span></div>`).join('')
    : `<p class="muted" style="padding:16px">Nichts gefunden.</p>`;
  $$('.palette-item', box).forEach((el) => el.addEventListener('click', () => {
    schliesseSuche();
    _sucheTreffer[Number(el.dataset.i)].run();
  }));
}

/* ---------------------------------------------------------------- Design */
function wendeDesignAn(theme) {
  const wert = theme || localStorage.getItem('kbweb.theme') || 'dark';
  localStorage.setItem('kbweb.theme', wert);
  if (wert === 'auto') {
    const hell = window.matchMedia('(prefers-color-scheme: light)').matches;
    document.documentElement.dataset.theme = hell ? 'light' : 'dark';
  } else {
    document.documentElement.dataset.theme = wert;
  }
}

/* ----------------------------------------------------------------- Start */
async function ladeServer() {
  try {
    const d = await api('/api/discord/guilds');
    STATE.guilds = d.server || [];
  } catch (_) {
    STATE.guilds = [];
  }
  const sel = $('#guildSelect');
  if (!STATE.guilds.length) {
    sel.innerHTML = '<option value="">kein Server erreichbar</option>';
    return;
  }
  sel.innerHTML = STATE.guilds.map((g) =>
    `<option value="${esc(g.id)}">${esc(g.name)}</option>`).join('');
  if (!STATE.guildId || !STATE.guilds.some((g) => g.id === STATE.guildId)) {
    STATE.guildId = STATE.guilds[0].id;
  }
  sel.value = STATE.guildId;
  localStorage.setItem('kbweb.guild', STATE.guildId);
}

async function aktualisiereKopf() {
  try {
    const d = await api('/api/overview');
    const pill = $('#botState');
    pill.className = `pill ${d.online ? 'online' : 'offline'}`;
    pill.lastElementChild.textContent = d.online ? 'Bot läuft' : 'Bot offline';
  } catch (_) { /* Kopfzeile ist Beiwerk — Fehler hier nicht melden */ }
  if (STATE.auth) {
    $('#tierState').textContent = STATE.auth.tier_label +
      (STATE.auth.may_critical ? '' : ' · eingeschränkt');
  }
}

async function starte() {
  $('#gate').hidden = true;
  $('#app').hidden = false;
  STATE.auth = await api('/api/auth/status');
  await ladeServer();
  aktualisiereKopf();
  const ausHash = (location.hash.match(/^#\/(\w+)/) || [])[1];
  gehZu(ausHash && TABS[ausHash] ? ausHash : 'uebersicht');
  setInterval(aktualisiereKopf, 30000);
}

function bindeGlobales() {
  $$('.nav-item').forEach((b) => b.addEventListener('click', () => gehZu(b.dataset.tab)));
  $('#refresh').addEventListener('click', () => { _kartenCache = null; zeichne(STATE.tab); });
  $('#menuOpen').addEventListener('click', () => $('#sidebar').classList.add('open'));
  $('#menuClose').addEventListener('click', () => $('#sidebar').classList.remove('open'));
  $('#searchTrigger').addEventListener('click', oeffneSuche);
  $('#guildSelect').addEventListener('change', (e) => {
    STATE.guildId = e.target.value;
    localStorage.setItem('kbweb.guild', STATE.guildId);
    zeichne(STATE.tab);
  });

  $$('[data-close]').forEach((el) => el.addEventListener('click', () => {
    schliesseDialog(); schliesseSuche();
  }));

  $('#paletteInput').addEventListener('input', (e) => sucheAktualisieren(e.target.value));

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      $('#palette').hidden ? oeffneSuche() : schliesseSuche();
      return;
    }
    if (e.key === 'Escape') { schliesseSuche(); schliesseDialog(); return; }
    if ($('#palette').hidden) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const richtung = e.key === 'ArrowDown' ? 1 : -1;
      _sucheAuswahl = (_sucheAuswahl + richtung + _sucheTreffer.length) % Math.max(_sucheTreffer.length, 1);
      zeichneSuche();
    } else if (e.key === 'Enter' && _sucheTreffer[_sucheAuswahl]) {
      e.preventDefault();
      schliesseSuche();
      _sucheTreffer[_sucheAuswahl].run();
    }
  });

  window.addEventListener('hashchange', () => {
    const tab = (location.hash.match(/^#\/(\w+)/) || [])[1];
    if (tab && TABS[tab] && tab !== STATE.tab) gehZu(tab);
  });
}

(async function haupt() {
  wendeDesignAn();
  bindeAnmeldung();
  bindeGlobales();
  try {
    if (await pruefeAnmeldung()) await starte();
  } catch (e) {
    zeigeAnmeldung();
    $('#gateError').hidden = false;
    $('#gateError').textContent = e.message;
  }
})();
