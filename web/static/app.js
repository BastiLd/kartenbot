/* ===========================================================================
   Kartenbot Web — Oberfläche

   Aufbau: ganz oben Werkzeuge (Anfragen, Formatierung, Meldungen), darunter
   ein Renderer je Bereich. Jeder Renderer bekommt sein Ziel-Element und baut
   seinen Inhalt selbst — es gibt bewusst kein Gerüst von außen, damit ein
   Bereich nie von einem anderen abhängt.
   =========================================================================== */
'use strict';

/* Die Fassung DIESER Datei. Sie kommt mit der ZIP, die Backend-Version per
   API — stimmen beide nicht überein, wurde nur eines der drei Teile
   aktualisiert. Siehe zeigeVersion() ganz unten.

   Beim Ausliefern mit web/VERSION gleichziehen. */
const OBERFLAECHE_VERSION = '1.3.0';

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
    // Ohne den Pfad ist "Unerwarteter Fehler (500)" nicht nachvollziehbar -
    // weder für den Benutzer noch für den, der es reparieren soll.
    const wo = String(pfad).split('?')[0];
    const roh = (text || '').trim().slice(0, 160);
    throw new Error((daten && (daten.error || daten.detail))
      || `Fehler ${antwort.status} bei ${wo}${roh ? ` — ${roh}` : ''}`);
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
  namen: {},          // Discord-ID -> Name, einmal geholt und dann wiederverwendet
  rollen: {},         // Rollen-ID -> Name, dito
  kanaele: {},        // Kanal-ID -> Name, dito
  quellen: { karte: [], rolle: [], mitglied: [], frei: [] },  // fuer die Auswahlfelder
  kartenAnsicht: localStorage.getItem('kbweb.kartenAnsicht') || 'liste',
  kartenBereich: localStorage.getItem('kbweb.kartenBereich') || 'helden',  // helden | schurken
  offeneKarte: null,    // Name der gross gezeigten Karte
  offenerSchurke: null, // Name des gross gezeigten Missionsgegners
};

/* ------------------------------------------------------------------ Namen -- */
/* In der Datenbank steht überall nur die ID. Hier wird daraus ein Name, und die
   ID rutscht klein und grau daneben — da stört sie nicht, ist aber ablesbar,
   wenn man sie doch mal braucht. */

function merkeNamen(paare) {
  if (paare) Object.assign(STATE.namen, paare);
}

/* Fehlende Namen nachladen und die Stellen im Text danach still ersetzen. */
async function ladeNamenNach(ids) {
  const offen = [...new Set(ids)].filter((i) => i && !STATE.namen[i]);
  if (!offen.length) return;
  try {
    const d = await api('/api/names', { json: { ids: offen } });
    merkeNamen(d.namen);
    // Alles, was schon gezeichnet ist, nachträglich auffrischen.
    $$('[data-person]').forEach((el) => {
      const name = STATE.namen[el.dataset.person];
      if (name) el.innerHTML = personInhalt(el.dataset.person, name);
    });
  } catch { /* Ohne Namen bleibt die ID stehen - kein Grund für eine Meldung. */ }
}

/* Einmal pro Server die ganze Mitgliederliste holen und alle Namen merken.
   Danach kennt die Seite jeden Namen, ohne Discord noch einmal zu fragen. */
let _aufgewaermt = null;
async function waermeNamenAuf(erzwingen = false) {
  if (!STATE.guildId) return;
  if (_aufgewaermt === STATE.guildId && !erzwingen) return;
  _aufgewaermt = STATE.guildId;
  try {
    await api(`/api/names/aufwaermen?guild_id=${encodeURIComponent(STATE.guildId)}`,
              { method: 'POST' });
    // Was schon auf dem Bildschirm steht, gleich mit Namen auffrischen.
    const offen = $$('[data-person]').map((el) => el.dataset.person);
    if (offen.length) await ladeNamenNach(offen);
  } catch {
    _aufgewaermt = null;   // beim nächsten Mal neu versuchen
  }
}

/* Im Protokoll stehen technische Schlüssel wie "papierkorb.zurueckgeholt".
   Die bleiben so gespeichert — sonst passte der alte Verlauf nicht mehr dazu.
   Angezeigt wird stattdessen deutscher Text. */
const AKTION_TEXT = {
  'waehrung.gegeben': 'Währung gegeben',
  'waehrung.entfernt': 'Währung weggenommen',
  'karte.gegeben': 'Karte gegeben',
  'karte.entfernt': 'Karte weggenommen',
  'seltenheit.gegeben': 'Seltenheitsgruppe gegeben',
  'seltenheit.entfernt': 'Seltenheitsgruppe weggenommen',
  'kanal.freigegeben': 'Kanal freigegeben',
  'kanal.gesperrt': 'Kanal gesperrt',
  'spieler.geloescht': 'Spielerdaten gelöscht',
  'papierkorb.zurueckgeholt': 'aus dem Papierkorb zurückgeholt',
  'papierkorb.endgueltig_geloescht': 'endgültig gelöscht',
  'datenbank.gesichert': 'Datenbank gesichert',
  'einstellungen.geaendert': 'Einstellungen geändert',
  'besitzer.beansprucht': 'Besitzer eingetragen',
  'besitzer.zurueckgesetzt': 'Besitzer zurückgesetzt',
  'auftrag.abgebrochen': 'Auftrag abgebrochen',
  'login.erfolgreich': 'angemeldet',
  'login.fehlgeschlagen': 'Anmeldung fehlgeschlagen',
  'login.fremdes_konto': 'fremdes Konto abgewiesen',
  'rollen.angewendet': 'Rollen geändert',
  'schalter.gesetzt': 'Schalter gesetzt',
};

function aktionText(schluessel) {
  // Unbekanntes lieber roh zeigen als verschlucken - dann sieht man wenigstens,
  // dass hier etwas fehlt.
  return AKTION_TEXT[schluessel] || schluessel;
}

/* Kanalnamen merken - dieselbe Idee wie bei Rollen. */
function merkeKanaele(kanaele) {
  (kanaele || []).forEach((c) => { if (c && c.id) STATE.kanaele[c.id] = c.name; });
}

function kanalName(id) {
  return STATE.kanaele[id] || id;
}

/* Rollennamen merken, damit im Verlauf nicht nur Nummern stehen. */
function merkeRollen(rollen) {
  (rollen || []).forEach((r) => { if (r && r.id) STATE.rollen[r.id] = r.name; });
}

function rollenName(id) {
  const name = STATE.rollen[id];
  return name
    ? `<strong>${esc(name)}</strong>`
    // Geloeschte Rollen kennt niemand mehr - dann bleibt die Nummer, damit
    // der Eintrag nicht sinnlos wird.
    : `<span class="mono">${esc(id)}</span>`;
}

function personInhalt(id, name) {
  if (!name) return `<span class="p-name mono">${esc(id)}</span>`;
  return `<span class="p-name">${esc(name)}</span><span class="p-id mono">${esc(id)}</span>`;
}

/* Eine Person darstellen: Name groß, ID klein daneben. */
function person(id) {
  if (!id) return '';
  return `<span class="person" data-person="${esc(id)}" title="${esc(id)}"
    >${personInhalt(id, STATE.namen[id])}</span>`;
}

/* Personenfeld mit Ausklapp-Pfeil und Vorschlagsliste darunter.

   Ein reines Textfeld zwingt dazu, Namen oder gar IDs auswendig zu wissen.
   Hier steht beim Tippen sofort darunter, wer gemeint sein könnte — und der
   Pfeil rechts zeigt die Liste auch ohne Eingabe, zum Durchschauen.
   Bedienbar auch ohne Maus: Pfeiltasten, Enter, Escape. */
function auswahlFeld(id, platzhalter = 'Name oder ID', quelle = 'person') {
  return `<div class="pfeld" data-pfeld data-quelle="${esc(quelle)}">
    <input id="${id}" class="pfeld-eingabe" placeholder="${esc(platzhalter)}"
           autocomplete="off" role="combobox" aria-expanded="false"
           aria-controls="${id}-liste">
    <button type="button" class="pfeld-pfeil" tabindex="-1"
            aria-label="Liste ausklappen">▾</button>
    <div class="pfeld-liste" id="${id}-liste" role="listbox" hidden></div>
  </div>`;
}

/* Alter Name, damit bestehende Aufrufe weiter gehen. */
function personenFeld(id, platzhalter = 'Name oder ID') {
  return auswahlFeld(id, platzhalter, 'person');
}

/* Woher die Vorschlaege kommen. Personen werden beim Server gesucht, alles
   andere steht schon auf der Seite und wird hier nur gefiltert. */
const QUELLEN = {
  person: async (text) => {
    if (!text) return [];
    const d = await api(`/api/names/search?q=${encodeURIComponent(text)}`);
    merkeNamen(Object.fromEntries(d.treffer.map((t) => [t.id, t.name])));
    return d.treffer;
  },
  karte: (text) => filtere(STATE.quellen.karte, text),
  rolle: (text) => filtere(STATE.quellen.rolle, text),
  mitglied: (text) => filtere(STATE.quellen.mitglied, text),
  frei: (text) => filtere(STATE.quellen.frei, text),
};

/* Ohne Eingabe die ganze Liste - der Pfeil soll ja zum Stoebern taugen. */
function filtere(liste, text) {
  // Eintraege ohne Namen waeren im Feld nur "undefined" - lieber gar nicht
  // anbieten, als etwas Sinnloses anzubieten.
  const alle = (liste || []).filter((e) => e && e.name);
  const k = (text || '').toLowerCase();
  if (!k) return alle.slice(0, 50);
  return alle.filter((e) => `${e.name} ${e.id || ''}`.toLowerCase().includes(k)).slice(0, 50);
}

function setzeQuelle(art, eintraege) {
  STATE.quellen[art] = eintraege || [];
}

function bindeNamensvorschlaege(wurzel) {
  $$('[data-pfeld]', wurzel).forEach((feld) => {
    if (feld.dataset.gebunden) return;        // nicht doppelt binden
    feld.dataset.gebunden = '1';
    const art = feld.dataset.quelle || 'person';
    const eingabe = $('.pfeld-eingabe', feld);
    const pfeil = $('.pfeld-pfeil', feld);
    const liste = $('.pfeld-liste', feld);
    let treffer = [];
    let aktiv = -1;
    let warten = null;

    const zu = () => {
      liste.hidden = true;
      eingabe.setAttribute('aria-expanded', 'false');
      aktiv = -1;
    };

    const zeichneListe = () => {
      if (!treffer.length) {
        liste.innerHTML = '<div class="pfeld-leer">Nichts gefunden.</div>';
      } else {
        liste.innerHTML = treffer.map((t, i) => `
          <div class="pfeld-treffer ${i === aktiv ? 'aktiv' : ''}" role="option"
               aria-selected="${i === aktiv}" data-i="${i}">
            <span class="pfeld-name">${esc(t.name)}</span>
            ${t.unter ? `<span class="pfeld-id">${esc(t.unter)}</span>`
                      : t.id ? `<span class="pfeld-id mono">${esc(t.id)}</span>` : ''}
          </div>`).join('');
      }
      liste.hidden = false;
      eingabe.setAttribute('aria-expanded', 'true');
    };

    const waehle = (i) => {
      const t = treffer[i];
      if (!t) return;
      eingabe.value = t.name;
      if (t.id) eingabe.dataset.id = t.id; else delete eingabe.dataset.id;
      if (art === 'person' && t.id) merkeNamen({ [t.id]: t.name });
      zu();
      // Suchfelder filtern eine Liste darunter - die muss jetzt nachziehen.
      eingabe.dispatchEvent(new Event('input', { bubbles: true }));
      eingabe.dispatchEvent(new Event('change', { bubbles: true }));
    };

    const suche = async (text) => {
      try {
        treffer = await QUELLEN[art](text);
        aktiv = -1;
        zeichneListe();
      } catch { zu(); }
    };

    eingabe.addEventListener('input', () => {
      delete eingabe.dataset.id;
      const text = eingabe.value.trim();
      clearTimeout(warten);
      if (!text && art === 'person') { zu(); return; }
      warten = setTimeout(() => suche(text), art === 'person' ? 180 : 0);
    });

    pfeil.addEventListener('click', () => {
      if (!liste.hidden) { zu(); return; }
      eingabe.focus();
      suche(eingabe.value.trim());
    });

    eingabe.addEventListener('keydown', (e) => {
      if (liste.hidden) {
        if (e.key === 'ArrowDown') { e.preventDefault(); suche(eingabe.value.trim()); }
        return;
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        aktiv += e.key === 'ArrowDown' ? 1 : -1;
        if (aktiv < 0) aktiv = treffer.length - 1;
        if (aktiv >= treffer.length) aktiv = 0;
        zeichneListe();
        const el = $('.pfeld-treffer.aktiv', liste);
        if (el) el.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter' && aktiv >= 0) {
        e.preventDefault(); waehle(aktiv);
      } else if (e.key === 'Escape') {
        zu();
      }
    });

    liste.addEventListener('mousedown', (e) => {
      const zeile = e.target.closest('[data-i]');
      if (zeile) { e.preventDefault(); waehle(Number(zeile.dataset.i)); }
    });

    eingabe.addEventListener('blur', () => setTimeout(zu, 120));
  });
}

/* Was steht wirklich im Feld? Wurde ein Vorschlag gewaehlt, ist es die ID -
   sonst das, was getippt wurde. Das Backend nimmt beides an. */
function personWert(feld) {
  if (!feld) return '';
  return feld.dataset.id || feld.value.trim();
}

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
    // Zentral nach jedem Zeichnen: jedes Personenfeld bekommt seine
    // Vorschlagsliste, und fehlende Namen werden nachgeholt. So muss kein
    // Bereich mehr selbst daran denken — auch kuenftige nicht.
    bindeNamensvorschlaege(ziel);
    const offen = $$('[data-person]', ziel).map((el) => el.dataset.person);
    if (offen.length) ladeNamenNach(offen);
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
  merkeNamen(d.namen);
  setzeQuelle('karte', karten.map((k) => ({ name: k.name, unter: k.seltenheit || '' })));

  ziel.innerHTML = `
    <div class="panel">
      <div class="panel-head"><h2>Spieler nachschlagen</h2>
        <p class="muted">Name oder Discord-ID eingeben — du bekommst alles zu dieser Person.</p></div>
      <div class="form-row">
        <label class="field"><span>Name oder Discord-ID</span>
          ${personenFeld('spSuche', 'z. B. Basti oder 965593518745731152')}</label>
        <div style="display:flex;align-items:flex-end"><button class="btn primary" id="spGo">Anzeigen</button></div>
      </div>
      <div id="spDetail" style="margin-top:20px"></div>
    </div>

    <div class="panel">
      <div class="panel-head"><h2>Geben und nehmen</h2>
        <p class="muted">Wirkt sofort in der Datenbank des Bots — genau wie die Befehle im Discord.</p></div>
      <div class="form-row">
        <label class="field"><span>An wen</span>
          ${personenFeld('aktUser')}
          <span class="hint">Auswählen fügt zur Liste hinzu — es gehen auch mehrere.</span></label>
        <label class="field"><span>Was</span>
          <select id="aktWas">
            <option value="infinitydust">Infinitydust</option>
            <option value="units">Units</option>
            <option value="karte">Karte</option>
            <option value="gruppe">Alle Karten einer Seltenheit</option>
          </select></label>
        <label class="field" id="aktKarteWrap" hidden><span>Karte</span>
          ${auswahlFeld('aktKarte', 'Name eingeben', 'karte')}</label>
        <label class="field" id="aktGruppeWrap" hidden><span>Seltenheit</span>
          <select id="aktGruppe"></select></label>
        <label class="field" id="aktMengeWrap"><span>Menge</span>
          <input id="aktMenge" type="number" min="1" value="1"></label>
      </div>
      <div id="aktEmpfaenger" class="chips" hidden></div>
      <details style="margin-top:10px">
        <summary class="muted" style="cursor:pointer">Viele auf einmal — Liste einfügen</summary>
        <div style="margin-top:10px">
          <textarea id="aktListe" rows="4" placeholder="Eine ID oder ein Name je Zeile"></textarea>
          <div class="form-actions" style="margin-top:8px">
            <button class="btn sm" id="aktListeUebernehmen">Zur Liste hinzufügen</button>
          </div>
        </div>
      </details>
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

  // Namen zu allen IDs in den Ranglisten nachladen, die noch fehlen.
  ladeNamenNach(Object.values(d).flatMap((v) => Array.isArray(v)
    ? v.map((z) => z && z.user_id).filter(Boolean) : []));
  bindeNamensvorschlaege(ziel);

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
    const id = personWert($('#spSuche', ziel));
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
  $('#spSuche', ziel).addEventListener('keydown', (e) => {
    // Nur absenden, wenn gerade KEIN Vorschlag ausgewaehlt wird - sonst
    // wuerde Enter beides zugleich tun.
    const liste = e.target.closest('[data-pfeld]')?.querySelector('.pfeld-liste');
    if (e.key === 'Enter' && (!liste || liste.hidden)) zeigeSpieler();
  });

  /* Empfängerliste. Eine Aktion soll nicht nur eine Person treffen können —
     wer zehn Leuten dasselbe geben will, soll es nicht zehnmal tippen. */
  const empfaenger = [];
  const zeichneEmpfaenger = () => {
    const box = $('#aktEmpfaenger', ziel);
    box.hidden = !empfaenger.length;
    box.innerHTML = empfaenger.map((e, i) => `
      <span class="chip">${esc(e.name || e.id)}
        <button type="button" class="chip-weg" data-i="${i}"
                aria-label="${esc(e.name || e.id)} entfernen">×</button></span>`).join('')
      + (empfaenger.length > 1
        ? `<button class="btn sm ghost" id="aktLeeren">alle ${empfaenger.length} entfernen</button>` : '');
    $$('.chip-weg', box).forEach((b) => b.addEventListener('click', () => {
      empfaenger.splice(Number(b.dataset.i), 1);
      zeichneEmpfaenger();
    }));
    const leeren = $('#aktLeeren', box);
    if (leeren) leeren.addEventListener('click', () => { empfaenger.length = 0; zeichneEmpfaenger(); });
  };

  const ergaenze = (id, name) => {
    if (!id || empfaenger.some((e) => e.id === id)) return;   // keine Doppelten
    empfaenger.push({ id, name: name || STATE.namen[id] || id });
    zeichneEmpfaenger();
  };

  // Wer im Feld etwas auswählt, landet in der Liste - das Feld wird wieder leer.
  const userFeld = $('#aktUser', ziel);
  userFeld.addEventListener('change', () => {
    const wert = personWert(userFeld);
    if (!wert) return;
    ergaenze(wert, userFeld.dataset.id ? userFeld.value : null);
    userFeld.value = '';
    delete userFeld.dataset.id;
  });

  $('#aktListeUebernehmen', ziel).addEventListener('click', async () => {
    const zeilen = $('#aktListe', ziel).value.split('\n').map((z) => z.trim()).filter(Boolean);
    if (!zeilen.length) return;
    let unbekannt = 0;
    for (const zeile of zeilen) {
      if (/^\d+$/.test(zeile)) { ergaenze(zeile); continue; }
      // Namen muessen erst zu einer ID werden - eindeutig oder gar nicht.
      try {
        const d = await api(`/api/names/search?q=${encodeURIComponent(zeile)}`);
        const genau = d.treffer.filter((t) => t.name.toLowerCase() === zeile.toLowerCase());
        const treffer = genau.length === 1 ? genau[0] : (d.treffer.length === 1 ? d.treffer[0] : null);
        if (treffer) ergaenze(treffer.id, treffer.name); else unbekannt++;
      } catch { unbekannt++; }
    }
    $('#aktListe', ziel).value = '';
    if (unbekannt) {
      toast(`${unbekannt} ${unbekannt === 1 ? 'Zeile war' : 'Zeilen waren'} nicht eindeutig `
            + 'und wurde nicht übernommen. Dort hilft die Discord-ID.', 'bad');
    }
  });

  const fuehreAus = async (entfernen) => {
    // Was noch im Feld steht, aber nicht bestätigt wurde, zählt trotzdem —
    // sonst wäre es überraschend, wenn nichts passiert.
    const offen = personWert(userFeld);
    if (offen) { ergaenze(offen); userFeld.value = ''; delete userFeld.dataset.id; }

    const ziele = empfaenger.map((e) => e.id);
    const was = wasFeld.value;
    const menge = parseInt($('#aktMenge', ziel).value, 10) || 1;
    if (!ziele.length) return toast('Bitte zuerst mindestens eine Person auswählen.', 'bad');
    const user = ziele[0];

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
          ${entfernen ? 'wird abgezogen von' : 'geht an'}
          ${ziele.length === 1
            ? empfaenger[0].name ? `<strong>${esc(empfaenger[0].name)}</strong>` : `<span class="mono">${esc(user)}</span>`
            : `<strong>${ziele.length} Personen</strong>`}
        </div>
        ${ziele.length > 1 ? `<div class="chips" style="margin-top:10px">${
          empfaenger.map((e) => `<span class="chip">${esc(e.name || e.id)}</span>`).join('')}</div>` : ''}`,
      gefahr: entfernen,
      knopfText: entfernen ? 'Ja, wegnehmen' : 'Ja, geben',
    });
    if (!ok) return;

    try {
      // Der Reihe nach, nicht alle gleichzeitig: die Datenbank des Bots hat
      // nur eine Schreibverbindung, und bei einem Fehler ist so klar, wo.
      const gelungen = [];
      const misslungen = [];
      let antwort = null;
      for (const id of ziele) {
        try {
          antwort = await api(pfad, { json: { ...koerper, user_id: id } });
          gelungen.push(id);
        } catch (e) {
          misslungen.push({ id, grund: e.message });
        }
      }

      if (!gelungen.length) {
        return fehler(new Error(misslungen[0] ? misslungen[0].grund : 'Nichts ausgeführt.'));
      }

      const text = ziele.length === 1
        ? erfolgstext(was, antwort, entfernen)
        : `${esc(beschreibung)} ${entfernen ? 'abgezogen von' : 'gegeben an'} `
          + `${gelungen.length} von ${ziele.length} Personen.`;
      toast(text, misslungen.length ? 'bad' : 'ok', {
        label: 'Rückgängig',
        run: async () => {
          // Nur das zurücknehmen, was wirklich gebucht wurde.
          for (const id of gelungen) {
            try { await api(pfad, { json: { ...koerper, user_id: id, remove: !entfernen } }); }
            catch (e) { fehler(e); }
          }
          toast(`Zurückgenommen (${gelungen.length}).`, 'ok');
          zeigeSpieler();
        },
      });
      if (misslungen.length) {
        toast(`${misslungen.length} fehlgeschlagen: ${esc(misslungen[0].grund)}`, 'bad');
      }
      empfaenger.length = 0;
      zeichneEmpfaenger();
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

function bestenliste(titel, zeilen, leerText = 'Noch keine Daten.') {
  const max = Math.max(1, ...zeilen.map((z) => Number(z.wert) || 0));
  return `<div class="panel"><div class="panel-head"><h3>${esc(titel)}</h3></div>
    ${zeilen.length ? zeilen.slice(0, 12).map((z) => `
      <div class="bar-row">
        <span class="name">${person(z.user_id)}</span>
        <span class="val">${num(z.wert)}</span>
        <span class="track"><span class="fill" style="width:${(Number(z.wert) || 0) / max * 100}%"></span></span>
      </div>`).join('') : leer(leerText)}
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

/* ------------------------------------------------------------------ Karten */
/* Zwei Ansichten: die Liste wie bisher, und Kacheln zum Stoebern. Ein Klick
   auf eine Kachel oeffnet die Karte gross - dort wird auch bearbeitet. */

const KNOPFFARBEN = { red: 'Rot', blurple: 'Blau', green: 'Grün', grey: 'Grau' };

/* Der Umschalter ganz oben: gruen die Helden, rot die Schurken.
   Zwei Welten, dieselbe Engine - beide lassen sich testen. */
function bereichsSchalter() {
  const b = STATE.kartenBereich;
  return `
    <div class="bereich-schalter">
      <button class="bereich helden ${b === 'helden' ? 'aktiv' : ''}" data-bereich="helden">
        <span class="bereich-icon">🦸</span>
        <span><strong>Helden</strong><small>Karten der Spieler</small></span>
      </button>
      <button class="bereich schurken ${b === 'schurken' ? 'aktiv' : ''}" data-bereich="schurken">
        <span class="bereich-icon">🦹</span>
        <span><strong>Schurken</strong><small>Missionen und Bosse</small></span>
      </button>
    </div>`;
}

function bindeBereichsSchalter(ziel) {
  $$('[data-bereich]', ziel).forEach((b) => b.addEventListener('click', () => {
    if (STATE.kartenBereich === b.dataset.bereich) return;
    STATE.kartenBereich = b.dataset.bereich;
    localStorage.setItem('kbweb.kartenBereich', STATE.kartenBereich);
    STATE.offeneKarte = null;
    STATE.offenerSchurke = null;
    zeichne('karten');
  }));
}

RENDER.karten = async (ziel, optionen = {}) => {
  if (STATE.kartenBereich === 'schurken') return zeichneSchurken(ziel, optionen);

  const [karten, geaendert] = await Promise.all([
    ladeKarten(),
    api('/api/karten/aenderungen').then((d) => d.aenderungen).catch(() => ({})),
  ]);
  setzeQuelle('karte', karten.map((k) => ({ name: k.name, unter: k.seltenheit || '' })));
  const seltenheiten = [...new Set(karten.map((k) => k.seltenheit).filter(Boolean))];

  // Eine einzelne Karte gross? Dann nur die zeichnen.
  const offen = optionen.karte || STATE.offeneKarte;
  if (offen) {
    const karte = karten.find((k) => k.name === offen);
    if (karte) return zeichneEinzelkarte(ziel, karte, geaendert[karte.name], seltenheiten);
    STATE.offeneKarte = null;      // Karte gibt es nicht mehr
  }

  const ansicht = STATE.kartenAnsicht;
  ziel.innerHTML = `
    ${bereichsSchalter()}
    <div class="panel">
      <div class="panel-head"><h2>Kartenkatalog</h2>
        <p class="muted">${num(karten.length)} Karten aus dem Spiel. Diese Liste kommt direkt aus
          dem Bot — was hier nicht steht, lässt sich auch nicht vergeben.
          ${Object.keys(geaendert).length
            ? `<br><strong>${Object.keys(geaendert).length}</strong> Karten sind über diese Seite geändert.`
            : ''}</p>
        <div class="spacer"></div>
        <div class="ansicht-schalter">
          <button class="btn sm ${ansicht === 'liste' ? 'primary' : 'ghost'}" data-ansicht="liste">☰ Liste</button>
          <button class="btn sm ${ansicht === 'kacheln' ? 'primary' : 'ghost'}" data-ansicht="kacheln">▦ Kacheln</button>
        </div>
      </div>
      <div class="form-row">
        <label class="field"><span>Suchen</span>${auswahlFeld('kSuche', 'Name oder Beschreibung', 'karte')}</label>
        <label class="field"><span>Seltenheit</span>
          <select id="kSeltenheit"><option value="">alle</option>
            ${seltenheiten.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join('')}
          </select></label>
      </div>
      <p class="muted" id="kAnzahl" style="margin-top:12px"></p>
      <div id="kListe" style="margin-top:8px"></div>
    </div>`;

  bindeBereichsSchalter(ziel);
  $$('[data-ansicht]', ziel).forEach((b) => b.addEventListener('click', () => {
    STATE.kartenAnsicht = b.dataset.ansicht;
    localStorage.setItem('kbweb.kartenAnsicht', STATE.kartenAnsicht);
    zeichne('karten');
  }));

  const zeichneListe = () => {
    const suche = $('#kSuche', ziel).value.trim().toLowerCase();
    const seltenheit = $('#kSeltenheit', ziel).value;
    const treffer = karten.filter((k) => {
      if (seltenheit && k.seltenheit !== seltenheit) return false;
      if (!suche) return true;
      return `${k.name} ${k.beschreibung || ''}`.toLowerCase().includes(suche);
    });
    $('#kAnzahl', ziel).textContent = `${num(treffer.length)} von ${num(karten.length)} Karten`;

    if (!treffer.length) {
      $('#kListe', ziel).innerHTML = leer('Keine Karte passt zu dieser Suche.', '🔍');
      return;
    }

    $('#kListe', ziel).innerHTML = ansicht === 'kacheln'
      ? `<div class="kachel-gitter">${treffer.map((k) => `
          <button class="kachel" data-oeffne="${esc(k.name)}">
            ${k.bild ? `<img src="${esc(k.bild)}" alt="" loading="lazy">`
                     : '<div class="kachel-ohne-bild">🃏</div>'}
            <div class="kachel-text">
              <strong>${esc(k.name)}</strong>
              <span class="muted">${esc(k.seltenheit || '?')} · ${num(k.hp)} HP</span>
              ${geaendert[k.name] ? '<span class="tag accent">geändert</span>' : ''}
            </div>
          </button>`).join('')}</div>`
      : treffer.map((k) => `
          <div class="bar-row karten-zeile" data-oeffne="${esc(k.name)}" role="button" tabindex="0">
            <span class="name"><strong>${esc(k.name)}</strong>
              <span class="muted"> · ${esc(k.seltenheit || '?')} · ${num(k.hp)} HP
              · ${k.angriffe.length} Angriffe</span>
              ${geaendert[k.name] ? '<span class="tag accent">geändert</span>' : ''}</span>
            <span class="val">›</span>
          </div>`).join('');

    $$('[data-oeffne]', ziel).forEach((el) => {
      const oeffne = () => { STATE.offeneKarte = el.dataset.oeffne; zeichne('karten'); };
      el.addEventListener('click', oeffne);
      el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); oeffne(); }
      });
    });
  };
  $('#kSuche', ziel).addEventListener('input', zeichneListe);
  $('#kSeltenheit', ziel).addEventListener('change', zeichneListe);
  zeichneListe();
};

/* ---------------------------------------------------------------- Schurken */
/* Die Gegner aus den Missionen. Jede Operation hat 3 kleine Gegner und einen
   Boss - und weil sie ganz normale Karten sind, laesst sich auch fuer sie
   ausrechnen, wie sie gegen die Spielerkarten dastehen. */

let _schurkenCache = null;
async function ladeSchurken() {
  if (_schurkenCache) return _schurkenCache;
  _schurkenCache = await api('/api/missionen');
  return _schurkenCache;
}

const ROLLEN = { boss: 'Boss', klein: 'kleiner Gegner' };

async function zeichneSchurken(ziel, optionen = {}) {
  const d = await ladeSchurken();
  const gegner = d.gegner || [];

  if (!d.verfuegbar || !gegner.length) {
    ziel.innerHTML = `${bereichsSchalter()}
      <div class="panel">${leer('Die Missionsgegner konnten nicht geladen werden. '
        + 'Ist der Bot-Ordner eingebunden?', '🦹')}</div>`;
    return bindeBereichsSchalter(ziel);
  }

  const offen = optionen.schurke || STATE.offenerSchurke;
  if (offen) {
    const einer = gegner.find((g) => g.name === offen);
    if (einer) return zeichneEinzelschurke(ziel, einer);
    STATE.offenerSchurke = null;
  }

  ziel.innerHTML = `
    ${bereichsSchalter()}
    <div class="panel">
      <div class="panel-head"><h2>Missionsgegner</h2>
        <p class="muted">${num(gegner.length)} Gegner aus ${d.operationen.length} Operationen.
          Jede hat drei kleine Gegner und einen Boss. Auch sie lassen sich testen —
          dann steht da, wie sie gegen die Karten der Spieler abschneiden.</p>
      </div>
      <div class="form-row">
        <label class="field"><span>Operation</span>
          <select id="sOperation"><option value="">alle</option>
            ${d.operationen.map((o) => `<option value="${esc(o.schluessel)}">${esc(o.name)}
              — Boss: ${esc(o.boss || '?')}</option>`).join('')}
          </select></label>
        <label class="field"><span>Art</span>
          <select id="sRolle"><option value="">alle</option>
            <option value="boss">nur Bosse</option>
            <option value="klein">nur kleine Gegner</option>
          </select></label>
        <label class="field"><span>Suchen</span>
          <input id="sSuche" placeholder="Name"></label>
      </div>
      <p class="muted" id="sAnzahl" style="margin-top:12px"></p>
      <div id="sListe" style="margin-top:8px"></div>
    </div>`;

  bindeBereichsSchalter(ziel);

  const zeichneListe = () => {
    const op = $('#sOperation', ziel).value;
    const rolle = $('#sRolle', ziel).value;
    const suche = $('#sSuche', ziel).value.trim().toLowerCase();
    const treffer = gegner.filter((g) => {
      if (op && g.operation !== op) return false;
      if (rolle && g.rolle !== rolle) return false;
      if (suche && !`${g.name} ${g.beschreibung || ''}`.toLowerCase().includes(suche)) return false;
      return true;
    });
    $('#sAnzahl', ziel).textContent = `${num(treffer.length)} von ${num(gegner.length)} Gegnern`;

    $('#sListe', ziel).innerHTML = treffer.length
      ? `<div class="kachel-gitter">${treffer.map((g) => `
          <button class="kachel ${g.rolle === 'boss' ? 'boss' : ''}" data-schurke="${esc(g.name)}">
            ${g.bild ? `<img src="${esc(g.bild)}" alt="" loading="lazy">`
                     : '<div class="kachel-ohne-bild">🦹</div>'}
            <div class="kachel-text">
              <strong>${esc(g.name)}</strong>
              <span class="muted">${esc(g.operation_name)} · ${num(g.hp)} HP</span>
              <span class="tag ${g.rolle === 'boss' ? 'accent' : ''}">${esc(ROLLEN[g.rolle] || g.rolle)}</span>
            </div>
          </button>`).join('')}</div>`
      : leer('Kein Gegner passt zu dieser Auswahl.', '🔍');

    $$('[data-schurke]', ziel).forEach((el) => el.addEventListener('click', () => {
      STATE.offenerSchurke = el.dataset.schurke;
      zeichne('karten');
    }));
  };
  $('#sOperation', ziel).addEventListener('change', zeichneListe);
  $('#sRolle', ziel).addEventListener('change', zeichneListe);
  $('#sSuche', ziel).addEventListener('input', zeichneListe);
  zeichneListe();
}

/* Ein Missionsgegner gross. Bewusst ohne Editor: Diese Gegner stehen im Bot
   und lassen sich nicht über die Website ändern - testen aber schon. */
async function zeichneEinzelschurke(ziel, g) {
  const laeufe = await api(`/api/karten/${encodeURIComponent(g.name)}/testlaeufe`)
    .then((d) => d.laeufe || []).catch(() => []);

  ziel.innerHTML = `
    ${bereichsSchalter()}
    <div class="panel karte-gross">
      <div class="karte-kopf">
        <button class="btn ghost sm" id="sZurueck" aria-label="Zurück zur Übersicht">← Zurück</button>
        <div class="spacer"></div>
        <span class="tag ${g.rolle === 'boss' ? 'accent' : ''}">${esc(ROLLEN[g.rolle] || g.rolle)}</span>
      </div>
      <div class="karte-oben">
        ${g.bild ? `<img class="karte-bild" src="${esc(g.bild)}" alt="">`
                 : '<div class="karte-bild karte-ohne-bild">🦹</div>'}
        <div class="karte-daten">
          <h2>${esc(g.name)}</h2>
          <p class="muted">${esc(g.operation_name)} · ${num(g.hp)} Lebenspunkte
            · ${g.angriffe.length} Angriffe
            ${g.passiv.length ? ` · ${g.passiv.length} passive Wirkungen` : ''}</p>
          ${g.beschreibung ? `<p>${esc(g.beschreibung)}</p>` : ''}
          <p class="hint">Missionsgegner stehen fest im Bot und lassen sich hier
            nicht ändern — testen aber schon.</p>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Testlauf</h3>
        <p class="muted">Wie schlägt sich dieser Gegner gegen die Karten der Spieler?
          ${g.rolle === 'boss' ? 'Ein Boss soll die meisten Kämpfe gewinnen — aber zu besiegen sein.'
                               : 'Ein Gegner der frühen Wellen soll fordern, nicht blockieren.'}</p>
        <div class="spacer"></div>
        <button class="btn primary gross" id="sTestlauf">⚔ Testlauf starten</button>
      </div>
      <div id="tlStatus"></div>
      <div id="tlErgebnis">${testlaufErgebnis(letzterBrauchbarerLauf(laeufe))}</div>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Angriffe</h3></div>
      ${g.angriffe.map((a, i) => `
        <details class="info-row" ${i === 0 ? 'open' : ''}>
          <summary><strong>${esc(a.name)}</strong>
            ${a.standard ? '<span class="tag accent">Standard</span>' : ''}
            ${a.schaden ? `<span class="tag">${esc(schadenAlsText(a.schaden))}</span>` : ''}</summary>
          <div class="why">
            ${a.info ? `<p>${esc(a.info)}</p>` : ''}
            <div class="mono" style="margin-top:8px">
              Schaden ${esc(schadenAlsText(a.schaden) || '—')}
              ${a.abklingzeit ? ` · Abklingzeit ${a.abklingzeit} Runden` : ''}
              ${a.heilung ? ` · heilt ${esc(schadenAlsText(a.heilung))}` : ''}
            </div>
            ${a.wirkungen.length ? `<p class="hint" style="margin-top:8px">
              Nebenwirkungen: ${a.wirkungen.map((w) => esc(w)).join(', ')}</p>` : ''}
          </div>
        </details>`).join('')}
      ${g.passiv.length ? `<p class="hint" style="margin-top:10px">
        Passive Wirkungen: ${g.passiv.map((p) => esc(p)).join(', ')}</p>` : ''}
    </div>`;

  bindeBereichsSchalter(ziel);
  bindeBeurteilen(ziel);
  $('#sZurueck', ziel).addEventListener('click', () => {
    STATE.offenerSchurke = null;
    zeichne('karten');
  });
  $('#sTestlauf', ziel).addEventListener('click', () => frageTestlauf(g));

  api('/api/jobs?limit=50').then((d) => {
    const offen = (d.laufend || []).find(
      (j) => j.kind === 'cards.testlauf' && (j.payload || {}).karte === g.name);
    if (offen) beobachteAuftrag(offen.id, $('#tlStatus', ziel));
  }).catch(() => {});
}

/* Eine Karte gross: Bild, alle Werte, Angriffe bearbeiten, Vorschau, Testlauf. */
async function zeichneEinzelkarte(ziel, k, aenderung, seltenheiten) {
  // Die frueheren Testlaeufe gleich mitladen. Faellt es aus, fehlt nur der
  // Ergebnisteil - die Karte selbst muss trotzdem bearbeitbar bleiben.
  const laeufe = await api(`/api/karten/${encodeURIComponent(k.name)}/testlaeufe`)
    .then((d) => d.laeufe || []).catch(() => []);

  ziel.innerHTML = `
    <div class="panel karte-gross">
      <div class="karte-kopf">
        <button class="btn ghost sm" id="kZurueck" aria-label="Zurück zur Übersicht">← Zurück</button>
        <div class="spacer"></div>
        <button class="btn sm" id="kVorschau">Vorschau</button>
      </div>

      <div class="karte-oben">
        ${k.bild ? `<img class="karte-bild" src="${esc(k.bild)}" alt="">`
                 : '<div class="karte-bild karte-ohne-bild">🃏</div>'}
        <div class="karte-daten">
          <h2>${esc(k.name)}</h2>
          <p class="muted">${esc(k.seltenheit || '?')} · ${num(k.hp)} Lebenspunkte
            · ${k.angriffe.length} Angriffe
            ${k.varianten.length ? ` · ${k.varianten.length} Varianten` : ''}</p>
          ${k.beschreibung ? `<p>${esc(k.beschreibung)}</p>` : ''}
          ${aenderung ? `<p class="hint">Geändert am ${zeitpunkt(aenderung.geaendert_am)}${
            aenderung.geaendert_von ? ` von ${esc(aenderung.geaendert_von)}` : ''}.</p>` : ''}
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Testlauf</h3>
        <p class="muted">Die Karte tritt gegen alle anderen an — mehrere hundert Kämpfe
          je Paarung. Danach steht schwarz auf weiß, ob sie zu stark, zu schwach oder
          rund ist.</p>
        <div class="spacer"></div>
        <button class="btn primary gross" id="kTestlaufGross">⚔ Testlauf starten</button>
      </div>
      <div id="tlStatus"></div>
      <div id="tlErgebnis">${testlaufErgebnis(letzterBrauchbarerLauf(laeufe))}</div>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Grunddaten</h3></div>
      <div class="form-row">
        <label class="field"><span>Lebenspunkte</span>
          <input type="number" min="1" max="10000" data-feld="hp" value="${esc(k.hp)}"></label>
        <label class="field"><span>Seltenheit</span>
          <select data-feld="seltenheit">${seltenheiten.map((s) =>
            `<option value="${esc(s)}" ${s === k.seltenheit ? 'selected' : ''}>${esc(s)}</option>`).join('')}
          </select></label>
      </div>
      <label class="field" style="margin-top:10px"><span>Beschreibung</span>
        <textarea rows="2" data-feld="beschreibung">${esc(k.beschreibung || '')}</textarea></label>
      <label class="field" style="margin-top:10px"><span>Bildadresse</span>
        <input data-feld="bild" value="${esc(k.bild || '')}" placeholder="https://..."></label>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Angriffe</h3>
        <p class="muted">Die Anzahl lässt sich nicht ändern — nur die vorhandenen bearbeiten.
          Genau einer muss der Standardangriff sein: der Knopf oben links im Kampf.</p></div>
      ${k.angriffe.map((a, i) => `
        <details class="info-row" ${i === 0 ? 'open' : ''} data-angriff="${i}">
          <summary><strong>${esc(a.name)}</strong>
            ${a.standard ? '<span class="tag accent">Standard</span>' : ''}
            ${a.schaden ? `<span class="tag">${esc(a.schaden)}</span>` : ''}</summary>
          <div class="why">
            <div class="form-row">
              <label class="field"><span>Name</span>
                <input data-a="name" value="${esc(a.name)}"></label>
              <label class="field"><span>Schaden</span>
                <input data-a="damage" value="${esc(schadenAlsText(a.schaden))}"
                       placeholder="z. B. 12 oder 11-15"></label>
              <label class="field"><span>Abklingzeit (Runden)</span>
                <input type="number" min="0" data-a="cooldown_turns"
                       value="${esc(a.abklingzeit ?? '')}"></label>
            </div>
            <label class="field" style="margin-top:10px"><span>Beschreibung</span>
              <input data-a="info" value="${esc(a.info || '')}"></label>
            <div class="form-row" style="margin-top:10px">
              <label class="field"><span>Knopffarbe</span>
                <select data-a="button_style">${Object.entries(KNOPFFARBEN).map(([w, t]) =>
                  `<option value="${w}" ${w === a.knopf ? 'selected' : ''}>${t}</option>`).join('')}
                </select></label>
              <label class="field"><span>Standardangriff</span>
                <select data-a="is_standard_attack">
                  <option value="0" ${a.standard ? '' : 'selected'}>nein</option>
                  <option value="1" ${a.standard ? 'selected' : ''}>ja</option>
                </select></label>
            </div>
            ${a.wirkungen && a.wirkungen.length ? `<p class="hint" style="margin-top:10px">
              Nebenwirkungen: ${a.wirkungen.map((w) => esc(w)).join(', ')} —
              die bleiben unverändert erhalten.</p>` : ''}
          </div>
        </details>`).join('')}
      <div class="form-actions" style="margin-top:14px">
        <button class="btn primary" id="kSpeichern">Alles speichern</button>
        ${aenderung ? '<button class="btn ghost" id="kZuruecksetzen">Wieder wie im Bot</button>' : ''}
      </div>
    </div>`;

  $('#kZurueck', ziel).addEventListener('click', () => {
    STATE.offeneKarte = null;
    zeichne('karten');
  });

  $('#kVorschau', ziel).addEventListener('click', () => zeigeVorschau(k));
  $('#kTestlaufGross', ziel).addEventListener('click', () => frageTestlauf(k));
  bindeBeurteilen(ziel);

  // Laeuft gerade schon einer fuer diese Karte? Dann direkt weiterverfolgen -
  // sonst stuende der Fortschritt still, nur weil die Seite neu geladen wurde.
  api('/api/jobs?limit=50').then((d) => {
    const offen = (d.laufend || []).find(
      (j) => j.kind === 'cards.testlauf' && (j.payload || {}).karte === k.name);
    if (offen) beobachteAuftrag(offen.id, $('#tlStatus', ziel));
  }).catch(() => {});

  $('#kSpeichern', ziel).addEventListener('click', async () => {
    const aenderungen = {};
    $$('[data-feld]', ziel).forEach((f) => { aenderungen[f.dataset.feld] = f.value; });
    aenderungen.attacks = $$('[data-angriff]', ziel).map((block) => {
      const eintrag = {};
      $$('[data-a]', block).forEach((f) => {
        const feld = f.dataset.a;
        if (feld === 'is_standard_attack') eintrag[feld] = f.value === '1';
        else if (feld === 'damage') eintrag[feld] = schadenAusText(f.value);
        else eintrag[feld] = f.value;
      });
      return eintrag;
    });
    try {
      await api('/api/karten/aendern', { json: { name: k.name, aenderungen } });
      toast(`„${k.name}“ gespeichert. Der Bot übernimmt es gleich.`, 'ok');
      _kartenCache = null;
      zeichne('karten');
    } catch (e) { fehler(e); }
  });

  const zurueck = $('#kZuruecksetzen', ziel);
  if (zurueck) zurueck.addEventListener('click', async () => {
    const ok = await bestaetige({
      titel: 'Zurück auf den Stand aus dem Bot?',
      vorschau: '<p>Alle Änderungen an dieser Karte werden verworfen. Der Verlauf bleibt erhalten.</p>',
      knopfText: 'Ja, zurücksetzen',
    });
    if (!ok) return;
    try {
      await api('/api/karten/zuruecksetzen', { json: { name: k.name } });
      toast('Zurückgesetzt.', 'ok');
      _kartenCache = null;
      zeichne('karten');
    } catch (e) { fehler(e); }
  });
}

/* [11, 15] wird zu "11-15", 12 bleibt "12". Umkehrung von schadenAusText. */
function schadenAlsText(wert) {
  if (Array.isArray(wert)) return `${wert[0]}-${wert[1]}`;
  return wert === null || wert === undefined ? '' : String(wert);
}

/* "11-15" wird zu [11, 15], "12" bleibt 12. Die Kartendatei nutzt beides. */
function schadenAusText(text) {
  const roh = String(text || '').trim();
  if (!roh) return '';
  const bereich = roh.match(/^(\d+)\s*(?:-|–|bis)\s*(\d+)$/);
  if (bereich) return [Number(bereich[1]), Number(bereich[2])];
  const zahl = Number(roh);
  return Number.isFinite(zahl) ? zahl : roh;
}

/* Zeigt die Karte so, wie der Bot sie im Discord ausgibt. */
function zeigeVorschau(k) {
  dialog({
    titel: 'So sieht die Karte im Discord aus',
    breit: true,
    inhalt: `
      <div class="discord-vorschau">
        <div class="dv-balken"></div>
        <div class="dv-inhalt">
          <div class="dv-titel">${esc(k.name)}</div>
          ${k.beschreibung ? `<div class="dv-text">${esc(k.beschreibung)}</div>` : ''}
          <div class="dv-felder">
            <div><strong>Seltenheit</strong><br>${esc(k.seltenheit || '?')}</div>
            <div><strong>Lebenspunkte</strong><br>${num(k.hp)}</div>
          </div>
          ${k.bild ? `<img class="dv-bild" src="${esc(k.bild)}" alt="">` : ''}
        </div>
      </div>
      <div class="dv-knoepfe">
        ${k.angriffe.map((a) => `
          <span class="dv-knopf dv-${esc(a.knopf || 'grey')}">${esc(a.name)}</span>`).join('')}
      </div>
      <p class="hint" style="margin-top:12px">Die Knöpfe zeigen die eingestellten Farben.
        Im Kampf steht der Standardangriff oben links.</p>`,
    knoepfe: [{ label: 'Schließen', art: 'primary' }],
  });
}

/* ---------------------------------------------------------------- Testlauf */
/* Der Bot rechnet, die Seite fragt nur nach und zeigt das Ergebnis. Ein Lauf
   dauert Minuten - deshalb Fortschritt und Abbruch wie beim Verlaufs-Scan. */

async function frageTestlauf(k) {
  let moeglich;
  try {
    moeglich = await api('/api/karten/testlauf/moeglichkeiten');
  } catch (e) { return fehler(e); }

  // Angetreten wird immer gegen die Spielerkarten. Ein Held tritt gegen alle
  // anderen an, ein Missionsgegner gegen alle - er ist ja selbst keine.
  const istSchurke = Boolean(k.rolle);
  const helden = (await ladeKarten()).length;
  const gegner = istSchurke ? helden : Math.max(0, helden - 1);
  const standard = moeglich.standard_kaempfe;

  dialog({
    titel: `Testlauf für „${k.name}“`,
    inhalt: `
      <p class="muted">„${esc(k.name)}“ tritt gegen ${istSchurke ? 'alle' : 'alle anderen'}
        ${num(gegner)} ${istSchurke ? 'Heldenkarten' : 'Karten'} an.
        Gerechnet wird im Bot, in kleinen Portionen — er bleibt dabei bedienbar,
        und du musst die Seite nicht offen lassen.</p>
      <div class="form-row" style="margin-top:14px">
        <label class="field"><span>Kämpfe je Paarung</span>
          <select id="tlKaempfe">
            ${moeglich.kampfzahlen.map((z) => `<option value="${z}" ${z === standard ? 'selected' : ''}>
              ${num(z)}${z === standard ? ' (empfohlen)' : ''}</option>`).join('')}
          </select></label>
        <label class="field"><span>Spielweise</span>
          <select id="tlSpielweise">
            ${moeglich.spielweisen.map((s) => `<option value="${esc(s.wert)}"
              ${s.wert === moeglich.standard_spielweise ? 'selected' : ''}>${esc(s.text)}</option>`).join('')}
          </select></label>
      </div>
      ${(moeglich.gelernte_versionen || []).length ? `
        <label class="field" style="margin-top:12px"><span>Womit gerechnet wird</span>
          <select id="tlVersion">
            <option value="0" selected>Standard — die eingebauten Gewichte</option>
            ${moeglich.gelernte_versionen.map((v) => `<option value="${v.id}">
              ${esc(v.name)} — gelernt aus ${num(v.zuege)} Entscheidungen</option>`).join('')}
          </select></label>
        <p class="hint" style="margin-top:6px">Eine Version, die aus echten Kämpfen
          gelernt hat, spielt im Testlauf anders — sie gewichtet Betäuben, Schützen,
          Vorbereiten und Dauerschaden so, wie es die Gewinner getan haben.</p>` : ''}
      <div class="notice" style="margin-top:14px" id="tlUmfang"></div>
      <p class="hint" style="margin-top:10px">Gerechnet wird auf Kopien — an der Karte und
        an der Datenbank ändert der Testlauf nichts.</p>`,
    knoepfe: [
      { label: 'Abbrechen', art: 'ghost' },
      {
        label: 'Testlauf starten',
        art: 'primary',
        run: async (zu) => {
          const versionFeld = $('#tlVersion');
          const koerper = {
            name: k.name,
            kaempfe_je_paarung: Number($('#tlKaempfe').value),
            spielweise: $('#tlSpielweise').value,
            version_id: versionFeld ? Number(versionFeld.value) || 0 : 0,
          };
          zu();
          try {
            const auftrag = await api('/api/karten/testlauf', { json: koerper });
            toast('Testlauf gestartet.', 'ok');
            beobachteAuftrag(auftrag.id, $('#tlStatus'));
          } catch (e) { fehler(e); }
        },
      },
    ],
  });

  const zeigeUmfang = () => {
    const je = Number($('#tlKaempfe').value);
    const gewaehlt = moeglich.spielweisen.find((s) => s.wert === $('#tlSpielweise').value);
    const runden = (gewaehlt && gewaehlt.durchgaenge) || 1;
    $('#tlUmfang').innerHTML = `Das sind <strong>${num(gegner * je * runden)} Kämpfe</strong>
      (${num(gegner)} Paarungen × ${num(je)}${runden > 1 ? ` × ${runden} Durchgänge` : ''}).
      Je nach Auslastung des Servers dauert das einige Minuten.
      Du kannst jederzeit abbrechen.`;
  };
  $('#tlKaempfe').addEventListener('change', zeigeUmfang);
  $('#tlSpielweise').addEventListener('change', zeigeUmfang);
  zeigeUmfang();
}

/* Welcher Lauf gezeigt wird.
   Wer einen Testlauf gleich nach dem Start abbricht, hinterlaesst eine Zeile
   ohne eine einzige fertige Paarung. Die duerfte das letzte richtige Ergebnis
   nicht verdecken - deshalb gewinnt der neueste Lauf, der etwas zu sagen hat. */
function letzterBrauchbarerLauf(laeufe) {
  return (laeufe || []).find((l) => durchgaengeVon(l).some((d) => d.paarungen?.length))
    || (laeufe || [])[0];
}

/* Ein Lauf kann mehrere Durchgaenge haben - einen je Spielweise. */
function durchgaengeVon(lauf) {
  const e = (lauf || {}).ergebnis || {};
  return e.durchgaenge || (e.paarungen ? [e] : []);
}

/* Den Knopf „Von der KI beurteilen lassen" scharf machen.
   Steht in beiden Einzelansichten - Helden wie Schurken. */
function bindeBeurteilen(ziel) {
  const knopf = $('[data-beurteilen]', ziel);
  if (!knopf) return;
  knopf.addEventListener('click', async () => {
    const alt = knopf.textContent;
    knopf.disabled = true;
    knopf.textContent = '🤖 Das Modell denkt nach …';
    try {
      await api(`/api/karten/testlaeufe/${knopf.dataset.beurteilen}/beurteilen`,
                { method: 'POST' });
      toast('Die Beurteilung ist da.', 'ok');
      zeichne('karten');
    } catch (e) {
      fehler(e);
      knopf.disabled = false;
      knopf.textContent = alt;
    }
  });
}

/* Das Ergebnis eines Laufs. Ohne Lauf ein Hinweis, sonst je Durchgang
   Einordnung, Kennzahlen und jede einzelne Paarung — und bei zwei
   Durchgaengen oben, was der Unterschied bedeutet. */
function testlaufErgebnis(lauf) {
  if (!lauf) {
    return leer('Für diese Karte gab es noch keinen Testlauf. Der Knopf oben startet einen.', '⚔');
  }
  if (lauf.status === 'running') {
    return `<div class="notice">Ein Testlauf läuft gerade — der Fortschritt erscheint oben.</div>`;
  }
  if (lauf.status === 'failed') {
    return `<div class="notice bad"><strong>Der letzte Testlauf ist fehlgeschlagen.</strong>
      ${lauf.error ? `<br>${esc(lauf.error)}` : ''}</div>`;
  }

  const e = lauf.ergebnis || {};
  const durchgaenge = durchgaengeVon(lauf);
  const karte = e.karte || lauf.karten_name;
  const mehrere = durchgaenge.length > 1;

  return `
    ${lauf.status === 'cancelled' ? `<div class="notice warn" style="margin-bottom:14px">
      Dieser Lauf wurde abgebrochen. Ausgewertet ist nur, was bis dahin fertig war.</div>` : ''}

    ${e.vergleich ? `<div class="notice" style="margin-bottom:14px">
      <strong>Bestmöglich gegen menschliches Spiel</strong><br>${esc(e.vergleich.text)}</div>` : ''}

    ${lauf.ki_text ? `<div class="notice ok" style="margin-bottom:14px">
      <strong>🤖 Beurteilung der KI</strong><br>${esc(lauf.ki_text)}
      <div class="hint" style="margin-top:8px">${esc(lauf.ki_modell || '')}${
        lauf.ki_am ? ` · ${esc(zeitpunkt(lauf.ki_am))}` : ''}</div></div>`
    : `<div style="margin-bottom:14px">
        <button class="btn sm" data-beurteilen="${lauf.id}">🤖 Von der KI beurteilen lassen</button>
        <span class="hint" style="margin-left:10px">Das Sprachmodell sagt in Worten,
          woran es liegt und was zu ändern wäre.</span>
      </div>`}

    ${durchgaenge.map((d) => testlaufDurchgang(d, karte, mehrere)).join('')}

    <p class="hint" style="margin-top:12px">
      ${esc(zeitpunkt(lauf.finished_at || lauf.started_at))}
      ${e.dauer_s ? ` · gerechnet in ${num(Math.round(e.dauer_s))} Sekunden` : ''}
      ${e.seed ? ` · Startwert ${e.seed}` : ''}
      ${e.gewichte && Object.keys(e.gewichte).length
        ? ' · gerechnet mit gelernten Gewichten' : ''}</p>`;
}

/* Ein einzelner Durchgang: eine Spielweise, alle Gegner. */
function testlaufDurchgang(d, karte, mitUeberschrift) {
  const ordnung = d.einordnung || {};
  const paarungen = d.paarungen || [];

  return `
    ${mitUeberschrift ? `<h4 class="tl-titel">${esc(TL_SPIELWEISEN[d.spielweise] || d.spielweise)}</h4>` : ''}

    ${ordnung.stufe ? `<div class="notice ${esc(ordnung.art || '')}">
      <strong>Einordnung: ${esc(ordnung.stufe)}</strong><br>${esc(ordnung.text)}</div>` : ''}

    <div class="grid cols-4" style="margin-top:14px">
      ${stat('Siegquote', `${num(d.siegquote ?? 0)} %`,
             `${num(d.siege)} Siege, ${num(d.niederlagen)} Niederlagen`,
             (d.siegquote ?? 0) >= 43 && (d.siegquote ?? 0) <= 57 ? 'good' : '')}
      ${stat('Kämpfe', num(d.kaempfe),
             `${num(paarungen.length)} Gegner × ${num(d.kaempfe_je_paarung)}`)}
      ${stat('Ø Runden', num(d.runden_schnitt ?? 0), 'je Kampf, beide Seiten zusammen')}
      ${stat('Unentschieden', num(d.unentschieden),
             d.unentschieden ? 'Kämpfe ohne Sieger' : 'keine')}
    </div>

    ${paarungen.length ? `<details class="info-row" ${mitUeberschrift ? '' : 'open'} style="margin-top:14px">
      <summary><strong>Alle ${num(paarungen.length)} Paarungen</strong>
        <span class="muted" style="margin-left:auto">schwerste Gegner zuerst</span></summary>
      <div class="why">
        ${paarungen.map((p) => `
          <div class="tl-paar">
            <span class="tl-gegner">${esc(p.gegner)}</span>
            <div class="progress tl-balken ${p.siegquote >= 80 ? 'gut' : p.siegquote <= 20 ? 'schlecht' : ''}">
              <i style="width:${Math.max(0, Math.min(100, p.siegquote))}%"></i></div>
            <span class="tl-quote">${num(p.siegquote)} %</span>
            <span class="tl-runden muted">${num(p.runden_schnitt)} R.</span>
          </div>`).join('')}
        <p class="hint" style="margin-top:10px">Der Balken zeigt, wie oft „${esc(karte)}“
          gegen diesen Gegner gewinnt. 50 % heißt: ausgeglichen.</p>
      </div>
    </details>` : ''}`;
}

const TL_SPIELWEISEN = { optimal: 'Bestmöglich gespielt', average: 'Wie Menschen, mit Fehlern' };

RENDER.statistik = async (ziel, options = {}) => {
  const zeitraum = options.zeitraum || '30d';
  const d = await api(`/api/statistics?range=${zeitraum}`);
  const bereiche = { today: 'Heute', '7d': '7 Tage', '30d': '30 Tage', '90d': '90 Tage', all: 'Gesamt' };

  const m = d.mitschrift || {};
  const laufend = (d.laufende_sitzungen || []).reduce((s, z) => s + (z.anzahl || 0), 0);

  ziel.innerHTML = `
    <div class="panel">
      <div class="panel-head"><h2>Zeitraum</h2>
        <div class="spacer"></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${Object.entries(bereiche).map(([k, l]) =>
            `<button class="btn sm ${k === zeitraum ? 'primary' : 'ghost'}" data-zeit="${k}">${l}</button>`).join('')}
        </div>
      </div>

      <div class="grid cols-4" style="margin-top:4px">
        ${stat('Kämpfe', num(d.kaempfe_gesamt), `${num(d.angriffe_gesamt)} Angriffe`)}
        ${stat('Ereignisse', num(d.ereignisse_gesamt), 'im gewählten Zeitraum')}
        ${stat('Läuft gerade', num(laufend),
               laufend ? (d.laufende_sitzungen || []).map((z) => `${z.anzahl}× ${z.name}`).join(', ')
                       : 'kein offener Kampf')}
        ${m.zuege ? stat('Ø Bedenkzeit', `${num(m.bedenkzeit_schnitt_s)} s`,
                         `aus ${num(m.zuege)} mitgeschriebenen Zügen`)
                  : stat('Ø Bedenkzeit', '—', 'Mitschrift ist aus')}
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Verlauf</h3>
        <p class="muted">Ereignisse je Tag — daran sieht man, ob etwas läuft.</p></div>
      ${(d.pro_tag || []).some((t) => t.anzahl) ? balken(d.pro_tag, 'tag', 'anzahl')
        : leer('Für diesen Zeitraum gibt es noch nichts.')}
    </div>

    <div class="grid cols-2">
      ${liste('Beliebteste Helden', d.top_helden, 'name', 'count')}
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
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Aktivität nach Uhrzeit</h3></div>
      ${d.pro_stunde.some((s) => s.anzahl) ? balken(d.pro_stunde, 'stunde', 'anzahl')
        : leer('Noch keine Aktivität aufgezeichnet.')}
    </div>

    <details class="info-row" style="margin-top:4px">
      <summary><strong>Mehr Zahlen</strong>
        <span class="muted" style="margin-left:auto">Befehle, Angriffe, Karten, Werbung</span></summary>
      <div class="why">
        <div class="grid cols-2" style="margin-top:8px">
          ${liste('Meistgenutzte Befehle', d.top_befehle, 'name', 'count', 'Noch keine Befehle aufgezeichnet.')}
          ${liste('Häufigste Züge', d.top_zuege, 'name', 'count')}
          ${liste('Meistbesessene Karten', d.top_karten, 'karten_name', 'anzahl')}
          ${bestenliste('Wer wirbt am meisten', (d.top_einlader || []).map((e) => ({
            user_id: e.user_id, wert: e.invited_count })),
            'Noch hat niemand jemanden geworben.')}
          ${(d.kampfarten || []).length ? liste('Kampfarten', d.kampfarten, 'name', 'anzahl') : ''}
          ${liste('Arten von Ereignissen', d.ereignistypen, 'name', 'count')}
        </div>
      </div>
    </details>

    ${d.dashboard_url ? `
      <div class="panel">
        <div class="panel-head"><h3>Noch genauer nachsehen</h3>
          <p class="muted">Das ausführliche Dashboard hat alles im Detail: Kampfverlauf,
            Einladungen, Prüfprotokolle, laufende Sitzungen.</p>
          <div class="spacer"></div>
          <a class="btn primary" href="${esc(d.dashboard_url)}" target="_blank" rel="noopener">
            Ausführliche Statistik öffnen ↗</a>
        </div>
      </div>` : ''}`;

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
  // Die Rollen sind ohnehin da - also im Verlauf den Namen zeigen statt der
  // Nummer. Nur wenn eine Rolle inzwischen geloescht wurde, bleibt die ID.
  merkeRollen(rollen);
  setzeQuelle('rolle', rollen.map((r) => ({ id: r.id, name: r.name,
    unter: r.manageable ? '' : 'kann der Bot nicht setzen' })));
  setzeQuelle('mitglied', mitglieder.map((m) => {
    const u = m.user || {};
    return { id: u.id, name: m.nick || u.global_name || u.username || u.id };
  }));

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
            ${auswahlFeld('mSuche', 'Name oder ID', 'mitglied')}
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
            <span>Rollen suchen</span>${auswahlFeld('rSuche', 'Rollenname', 'rolle')}
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
          <label class="field"><span>Name oder Discord-ID</span>
            ${personenFeld('permId')}</label>
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
    const id = personWert($('#permId', ziel));
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
          ${person(v.user_id)} ·
          ${v.action === 'add' ? 'bekam' : 'verlor'} Rolle ${rollenName(v.role_id)}
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
        <p class="muted">${num(profile.profile.length)} Mitglieder ausgewertet.
          ${setzeQuelle('frei', profile.profile.map((p) => ({
            id: p.user_id,
            name: (p.stats && p.stats.name) || STATE.namen[p.user_id] || p.user_id,
            unter: (p.tags || []).map((t) => t.label || t).join(', '),
          }))) || ''}</p>
        <div class="spacer"></div>
        <div style="max-width:260px;width:100%">${auswahlFeld('pSuche', 'Name oder Einordnung suchen', 'frei')}</div>
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
        <summary><strong>${s.name ? esc(s.name) : person(p.user_id)}</strong>
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
        // Nach einem Testlauf die Karte neu zeichnen - dann steht das Ergebnis
        // dort, wo eben noch der Fortschritt war. Auch nach einem Abbruch,
        // denn auch der hinterlaesst ein Teilergebnis.
        if (STATE.tab === 'karten' && job.kind === 'cards.testlauf'
            && ['done', 'cancelled'].includes(job.status)) {
          zeichne('karten', { leise: true });
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
  const gid = STATE.guildId;
  // Die Kanalliste kommt von Discord, die Freigaben aus der Datenbank des Bots.
  const [d, kanalInfo] = await Promise.all([
    api('/api/guild-settings'),
    gid ? api(`/api/discord/${gid}/channels`).catch(() => null) : null,
  ]);
  const server = d.server || [];
  const kanaele = (kanalInfo && kanalInfo.kanaele) || [];
  const eigene = server.find((s) => String(s.guild_id) === String(gid));
  const frei = new Set((eigene ? eigene.erlaubte_kanaele : []).map(String));

  ziel.innerHTML = `
    ${gid ? `
    <div class="panel">
      <div class="panel-head"><h2>Kanal-Freigaben</h2>
        <p class="muted">Wo der Bot auf Befehle antwortet. ${frei.size
          ? `Zurzeit <strong>${frei.size}</strong> ${frei.size === 1 ? 'Kanal' : 'Kanäle'} freigegeben —
             überall sonst bleibt er still.`
          : '<strong>Zurzeit ist nichts eingeschränkt</strong>, der Bot antwortet in jedem Kanal. '
            + 'Sobald du den ersten Kanal freigibst, gilt nur noch dieser.'}</p></div>
      ${kanaele.length ? `
        <div class="pick-list" id="kanalListe">
          ${kanaele.map((c) => `
            <label class="pick">
              <input type="checkbox" class="kPick" value="${esc(c.id)}"
                     ${frei.has(String(c.id)) ? 'checked' : ''}>
              <span><strong>#${esc(c.name)}</strong>
                ${c.kategorie ? `<br><span class="muted">${esc(c.kategorie)}</span>` : ''}</span>
            </label>`).join('')}
        </div>`
        : leer('Keine Kanäle gefunden. Ist der Bot-Token hinterlegt?')}
    </div>` : ''}

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
              ? s.erlaubte_kanaele.map((c) => `<span class="tag">#${esc(kanalName(c))}</span>`).join(' ')
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
      <div class="panel-head"><h3>Datenbank</h3>
        <p class="muted">Kopie ziehen, bevor du etwas Größeres vorhast.</p></div>
      <div id="dbZustand"><div class="skeleton" style="width:45%"></div></div>
      <div class="form-actions" style="margin-top:12px">
        <a class="btn" href="/api/datenbank/sicherung" download>Kopie herunterladen</a>
        <a class="btn ghost" href="/api/bericht.csv?bereich=spieler" download>Spieler als CSV</a>
        <a class="btn ghost" href="/api/bericht.csv?bereich=protokoll" download>Protokoll als CSV</a>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Papierkorb</h3>
        <p class="muted">Gelöschtes bleibt hier liegen und lässt sich zurückholen.</p></div>
      <div id="papierkorbListe"><div class="skeleton"></div></div>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Protokoll der Website</h3>
        <p class="muted">Jede Aktion, die hier ausgelöst wurde.</p></div>
      <div id="auditListe"><div class="skeleton"></div></div>
    </div>`;

  // Zustand der Datenbank
  api('/api/datenbank/pruefen').then((p) => {
    $('#dbZustand', ziel).innerHTML = `
      <div class="notice ${p.in_ordnung ? 'ok' : 'bad'}">
        ${p.in_ordnung
          ? `Die Datenbank ist in Ordnung — ${p.tabellen} Tabellen, ${bytes(p.groesse)}.`
          : `Die Prüfung meldet: <strong>${esc(p.ergebnis)}</strong>
             ${p.verwaiste_verweise ? `<br>${p.verwaiste_verweise} verwaiste Verweise.` : ''}`}
      </div>`;
  }).catch((e) => { $('#dbZustand', ziel).innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; });

  // Papierkorb
  const ladePapierkorb = async () => {
    const box = $('#papierkorbListe', ziel);
    try {
      const d = await api('/api/papierkorb');
      box.innerHTML = d.eintraege.length ? d.eintraege.map((e) => `
        <div class="bar-row"><span class="name">
          <strong>${esc(e.titel)}</strong>
          <span class="muted"> · ${e.anzahl} ${e.anzahl === 1 ? 'Eintrag' : 'Einträge'}
          ${e.actor ? ` · durch ${esc(e.actor)}` : ''}</span>
          ${e.zurueckgeholt_am ? '<span class="tag ok">zurückgeholt</span>' : ''}
        </span>
        <span class="val" style="display:flex;gap:6px;align-items:center">
          <span class="muted">${zeitpunkt(e.erstellt_am)}</span>
          ${e.zurueckgeholt_am ? '' :
            `<button class="btn sm" data-zurueck="${e.id}">Zurückholen</button>`}
          <button class="btn sm danger" data-weg="${e.id}">Endgültig weg</button>
        </span></div>`).join('')
        : leer(`Nichts im Papierkorb. Gelöschtes bleibt ${d.aufbewahrung_tage} Tage liegen.`);

      $$('[data-zurueck]', box).forEach((b) => b.addEventListener('click', async () => {
        try {
          const r = await api(`/api/papierkorb/${b.dataset.zurueck}/zurueckholen`, { method: 'POST' });
          toast(`${r.gesamt} ${r.gesamt === 1 ? 'Eintrag' : 'Einträge'} zurückgeholt.`, 'ok');
          if (r.fehler && r.fehler.length) toast(`Teilweise fehlgeschlagen: ${esc(r.fehler[0])}`, 'bad');
          ladePapierkorb();
        } catch (e) { fehler(e); }
      }));

      $$('[data-weg]', box).forEach((b) => b.addEventListener('click', async () => {
        const ok = await bestaetige({
          titel: 'Endgültig löschen?',
          warnung: 'Danach ist es wirklich weg — es gibt keinen zweiten Papierkorb.',
          knopfText: 'Ja, endgültig löschen',
        });
        if (!ok) return;
        try {
          await api(`/api/papierkorb/${b.dataset.weg}`, { method: 'DELETE' });
          toast('Endgültig gelöscht.', '');
          ladePapierkorb();
        } catch (e) { fehler(e); }
      }));
    } catch (e) { box.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
  };
  ladePapierkorb();

  merkeKanaele(kanaele);

  // Ein Häkchen gibt einen Kanal frei oder sperrt ihn wieder.
  $$('.kPick', ziel).forEach((box) => box.addEventListener('change', async () => {
    const name = box.closest('.pick').querySelector('strong').textContent;
    const vorher = !box.checked;
    try {
      await api('/api/actions/channel',
                { json: { guild_id: gid, channel_id: box.value, allow: box.checked } });
      toast(`${name} ${box.checked ? 'freigegeben' : 'gesperrt'}.`, 'ok', {
        label: 'Rückgängig',
        run: async () => {
          try {
            await api('/api/actions/channel',
                      { json: { guild_id: gid, channel_id: box.value, allow: vorher } });
            zeichne('steuerung');
          } catch (e) { fehler(e); }
        },
      });
      // Der einleitende Satz haengt an der Anzahl - also neu zeichnen.
      zeichne('steuerung', { leise: true });
    } catch (e) {
      box.checked = vorher;      // die Anzeige darf nicht luegen
      fehler(e);
    }
  }));

  $$('[data-flag]', ziel).forEach((b) => b.addEventListener('click', async () => {
    const { guild, flag, wert } = b.dataset;
    const neu = wert !== '1';
    const name = b.textContent.split(':')[0].trim();
    try {
      await api('/api/actions/flag', { json: { guild_id: guild, flag, enabled: neu } });
      toast(`${name} ist jetzt ${neu ? 'an' : 'aus'}.`, 'ok', {
        label: 'Rückgängig',
        run: async () => {
          try {
            await api('/api/actions/flag', { json: { guild_id: guild, flag, enabled: !neu } });
            zeichne('steuerung');
          } catch (e) { fehler(e); }
        },
      });
      zeichne('steuerung');
    } catch (e) { fehler(e); }
  }));

  try {
    const a = await api('/api/audit?limit=60');
    $('#auditListe', ziel).innerHTML = a.eintraege.length ? a.eintraege.map((e) => `
      <div class="bar-row"><span class="name">
        ${e.ok ? '' : '<span class="tag bad">Fehler</span> '}
        <strong>${esc(aktionText(e.action))}</strong> ${e.target ? `· ${/^\d+$/.test(e.target) ? person(e.target) : esc(e.target)}` : ''}
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
/* Was die Zug-Mitschrift bisher gesammelt hat.
   Steht bewusst ueber dem Schalter: Wer ihn umlegt, soll vorher sehen, was
   da ist - gesammelt wird nie still. */
function mitschriftStand(m) {
  if (!m) return '';
  if (!m.zuege) {
    return `<div class="notice" style="margin-bottom:16px">Noch nichts aufgezeichnet.
      Solange der Schalter aus ist, wird auch nichts gesammelt.</div>`;
  }
  const arten = { fight_pvp: 'gegen Mitspieler', fight_bot: 'gegen den Bot', mission: 'Missionen' };
  return `
    <div class="notice ok" style="margin-bottom:16px">
      <strong>${num(m.zuege)} Züge aus ${num(m.kaempfe)} Kämpfen</strong> aufgezeichnet${
        m.seit ? `, seit ${esc(zeitpunkt(m.seit))}` : ''}.<br>
      Davon zum Lernen brauchbar: <strong>${num(m.verwertbar)}</strong> —
      nur Züge aus Kämpfen, die zu Ende gespielt wurden.
      ${m.vom_bot ? `<br>${num(m.vom_bot)} davon sind Züge des Bots selbst.` : ''}
      ${(m.nach_art || []).length ? `<br><span class="muted">${m.nach_art.map((a) =>
        `${esc(arten[a.kampf_art] || a.kampf_art || 'unbekannt')}: ${num(a.zuege)}`).join(' · ')}</span>` : ''}
    </div>`;
}

/* ------------------------------------------------------- Gegner-Versionen */
/* Anlegen, bearbeiten, kopieren, loeschen - und festlegen, welche gilt.
   "Standard" ist fest eingebaut: nicht aenderbar, nicht loeschbar. Es muss
   immer einen Weg zurueck zu "spielt wie immer" geben. */
async function zeichneVersionen(wurzel) {
  const box = $('#versionenListe', wurzel);
  if (!box) return;
  let d;
  try {
    d = await api('/api/gegner-versionen');
  } catch (e) {
    box.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
    return;
  }
  const aktivFuerAlle = Number(d.aktiv['*'] || 0);
  const stoff = d.lernstoff || { zuege: 0, kaempfe: 0, mitgeschrieben: 0 };

  /* Was aus echten Kaempfen gelernt wurde - je Version. Steht nur da, wenn
     wirklich gelernt wurde; ein leerer Kasten waere nur Platzverbrauch. */
  const lernBlock = (v) => {
    const stand = v.lernstand || {};
    const gewichte = v.gewichte || {};
    const namen = {
      control: 'Betäuben und Sperren',
      defense: 'Schützen und Ausweichen',
      setup: 'Vorbereiten',
      dot: 'Schaden über Runden',
    };
    const gelernt = Object.keys(gewichte).length > 0;
    return `
      <div class="lern-block">
        <h4>Aus echten Kämpfen gelernt</h4>
        ${gelernt ? `
          <div class="lern-werte">
            ${Object.entries(namen).map(([k, text]) => {
              const werte = (stand.grundlage || {})[k] || {};
              const grund = werte.grundgewicht;
              const neu = gewichte[k];
              const anders = werte.gelernt && neu !== grund;
              return `<div class="bar-row">
                <span class="name">${text}</span>
                <span class="val ${anders ? 'accent' : 'muted'}">
                  ${werte.gelernt ? `${grund} → ${neu}` : 'zu wenig Material'}</span>
              </div>`;
            }).join('')}
          </div>
          <p class="hint" style="white-space:pre-line">${esc(stand.text || '')}</p>
          <p class="hint">Gelernt am ${zeitpunkt(stand.stand_am)}.
            Die Gewichte wirken im <strong>Testlauf</strong> — der Kampf im Discord
            spielt unverändert weiter.</p>
          <div class="form-actions">
            <button class="btn sm" data-lernen="${v.id}">Neu lernen</button>
            <button class="btn danger sm" data-verlernen="${v.id}">Gelerntes verwerfen</button>
          </div>`
        : `
          <p class="hint">Noch nichts gelernt — diese Version rechnet mit den
            eingebauten Gewichten.</p>
          <p class="hint">Zur Verfügung stehen <strong>${num(stoff.zuege)}</strong>
            Entscheidungen aus <strong>${num(stoff.kaempfe)}</strong> gewonnenen
            Kämpfen${stoff.mitgeschrieben ? '' : ' — die Zug-Mitschrift ist noch aus'}.</p>
          <div class="form-actions">
            <button class="btn sm" data-lernen="${v.id}">Aus echten Kämpfen lernen</button>
          </div>`}
      </div>`;
  };

  box.innerHTML = `
    <label class="field" style="max-width:520px">
      <span>Welche Version gilt (für alle Server)</span>
      <select id="vAktiv">
        ${d.versionen.map((v) => `<option value="${v.id}" ${v.id === aktivFuerAlle ? 'selected' : ''}>
          ${esc(v.name)}${v.fehlerquote ? ` — Fehlerquote ${v.fehlerquote}` : ''}</option>`).join('')}
      </select>
    </label>

    <div style="margin-top:18px">
      ${d.versionen.map((v) => `
        <details class="info-row" data-version="${v.id}">
          <summary><strong>${esc(v.name)}</strong>
            ${v.ist_standard ? '<span class="tag">fest eingebaut</span>' : ''}
            ${v.id === aktivFuerAlle ? '<span class="tag accent">gilt gerade</span>' : ''}
            <span class="muted" style="margin-left:auto">Fehlerquote ${v.fehlerquote}</span></summary>
          <div class="why">
            <p>${esc(v.beschreibung || 'Keine Beschreibung.')}</p>
            ${v.ist_standard ? `<p class="hint">Diese Version lässt sich nicht ändern.
              Wenn du etwas anderes willst, kopiere sie und ändere die Kopie.</p>
              <div class="form-actions"><button class="btn sm" data-kopieren="${v.id}">Kopieren</button></div>`
            : `<div class="form-row">
                <label class="field"><span>Name</span>
                  <input data-v="name" value="${esc(v.name)}"></label>
                <label class="field"><span>Fehlerquote (0 bis ${d.max_fehlerquote})</span>
                  <input type="number" step="0.05" min="0" max="${d.max_fehlerquote}"
                         data-v="fehlerquote" value="${v.fehlerquote}"></label>
              </div>
              <label class="field" style="margin-top:10px"><span>Beschreibung</span>
                <textarea rows="2" data-v="beschreibung">${esc(v.beschreibung || '')}</textarea></label>
              <p class="hint" style="margin-top:6px">Die Beschreibung sieht man später im
                Discord bei der Auswahl — schreib hin, worauf man sich einlässt.</p>
              <div class="form-actions">
                <button class="btn primary sm" data-speichern="${v.id}">Speichern</button>
                <button class="btn sm" data-kopieren="${v.id}">Kopieren</button>
                <button class="btn danger sm" data-loeschen="${v.id}">Löschen</button>
              </div>
              ${lernBlock(v)}`}
          </div>
        </details>`).join('')}
    </div>

    <div class="form-row" style="margin-top:16px">
      <label class="field"><span>Neue Version anlegen</span>
        <input id="vNeuName" placeholder="z. B. Schwer"></label>
      <label class="field"><span>Fehlerquote</span>
        <input id="vNeuQuote" type="number" step="0.05" min="0" max="${d.max_fehlerquote}" value="0"></label>
    </div>
    <div class="form-actions">
      <button class="btn" id="vAnlegen">Anlegen</button>
    </div>
    <p class="hint">Fehlerquote 0 = der Bot spielt immer den besten Zug.
      0,3 = er greift in rund jedem dritten Zug absichtlich daneben.</p>`;

  const neuLaden = () => zeichneVersionen(wurzel);

  $('#vAktiv', box).addEventListener('change', async (e) => {
    try {
      await api('/api/gegner-versionen/aktiv', { json: { version_id: Number(e.target.value) } });
      toast('Gilt ab jetzt. Laufende Kämpfe behalten ihre Einstellung.', 'ok');
      neuLaden();
    } catch (err) { fehler(err); }
  });

  $('#vAnlegen', box).addEventListener('click', async () => {
    try {
      await api('/api/gegner-versionen', { json: {
        name: $('#vNeuName', box).value,
        fehlerquote: Number($('#vNeuQuote', box).value) || 0,
      } });
      toast('Version angelegt.', 'ok');
      neuLaden();
    } catch (err) { fehler(err); }
  });

  $$('[data-speichern]', box).forEach((b) => b.addEventListener('click', async () => {
    const block = b.closest('[data-version]');
    try {
      await api(`/api/gegner-versionen/${b.dataset.speichern}`, {
        method: 'PUT',
        json: {
          name: $('[data-v="name"]', block).value,
          beschreibung: $('[data-v="beschreibung"]', block).value,
          fehlerquote: Number($('[data-v="fehlerquote"]', block).value) || 0,
        },
      });
      toast('Gespeichert.', 'ok');
      neuLaden();
    } catch (err) { fehler(err); }
  }));

  $$('[data-kopieren]', box).forEach((b) => b.addEventListener('click', async () => {
    const name = prompt('Wie soll die Kopie heißen?');
    if (!name) return;
    try {
      await api(`/api/gegner-versionen/${b.dataset.kopieren}/kopieren`, { json: { name } });
      toast('Kopiert.', 'ok');
      neuLaden();
    } catch (err) { fehler(err); }
  }));

  $$('[data-lernen]', box).forEach((b) => b.addEventListener('click', async () => {
    const vorher = b.textContent;
    b.disabled = true;
    b.textContent = 'Rechnet …';
    try {
      const v = await api(`/api/gegner-versionen/${b.dataset.lernen}/lernen`, { method: 'POST' });
      toast(`Gelernt aus ${num((v.lernstand || {}).zuege_verwertet || 0)} Entscheidungen.`, 'ok');
      neuLaden();
    } catch (err) {
      b.disabled = false;
      b.textContent = vorher;
      fehler(err);
    }
  }));

  $$('[data-verlernen]', box).forEach((b) => b.addEventListener('click', async () => {
    const ok = await bestaetige({
      titel: 'Das Gelernte verwerfen?',
      vorschau: '<p>Diese Version rechnet danach wieder mit den eingebauten Gewichten. '
        + 'Die mitgeschriebenen Züge bleiben erhalten — neu lernen geht jederzeit.</p>',
      knopfText: 'Ja, verwerfen',
    });
    if (!ok) return;
    try {
      await api(`/api/gegner-versionen/${b.dataset.verlernen}/lernen`, { method: 'DELETE' });
      toast('Verworfen.', 'ok');
      neuLaden();
    } catch (err) { fehler(err); }
  }));

  $$('[data-loeschen]', box).forEach((b) => b.addEventListener('click', async () => {
    const ok = await bestaetige({
      titel: 'Diese Version löschen?',
      vorschau: '<p>Wo sie gerade gilt, spielt der Bot danach wieder wie „Standard“.</p>',
      knopfText: 'Ja, löschen',
    });
    if (!ok) return;
    try {
      await api(`/api/gegner-versionen/${b.dataset.loeschen}`, { method: 'DELETE' });
      toast('Gelöscht.', 'ok');
      neuLaden();
    } catch (err) { fehler(err); }
  }));
}

RENDER.einstellungen = async (ziel) => {
  const [d, ki, modelle, test, mitschrift] = await Promise.all([
    api('/api/settings'),
    api('/api/ai/status').catch((e) => ({ ok: false, error: e.message })),
    // Nur fürs Auswahlfeld. Ist Ollama gerade weg, bleibt es eben ein Textfeld.
    api('/api/ai/models').then((m) => m.modelle).catch(() => null),
    api('/api/selbsttest').catch((e) => ({ pruefungen: [], fehler: e.message })),
    api('/api/mitschrift').catch(() => null),
  ]);
  const gruppen = {};
  d.einstellungen.forEach((e) => { (gruppen[e.group] = gruppen[e.group] || []).push(e); });

  ziel.innerHTML = `
    <div class="panel">
      <div class="panel-head"><h2>Selbsttest</h2>
        <p class="muted">Was funktioniert gerade, und was nicht.</p></div>
      ${(test.pruefungen || []).length ? `<div class="pruefliste">
        ${test.pruefungen.map((p) => `
          <div class="pruefzeile">
            <span class="pruef-zeichen ${p.ok ? 'ok' : 'bad'}">${p.ok ? '✓' : '✕'}</span>
            <span class="pruef-name">${esc(p.name)}</span>
            <span class="pruef-text ${p.ok ? 'muted' : ''}">${esc(p.text)}</span>
          </div>
          ${p.hinweis ? `<div class="pruef-hinweis">${esc(p.hinweis)}</div>` : ''}`).join('')}
      </div>` : `<div class="notice bad">Selbsttest nicht möglich: ${esc(test.fehler || '')}</div>`}
    </div>

    <div class="panel">
      <div class="panel-head"><h2>KI-Verbindung</h2></div>
      <div class="notice ${ki.ok ? 'ok' : 'warn'}">
        ${ki.ok ? `Ollama erreichbar unter <span class="mono">${esc(ki.url)}</span> (${esc(ki.version || '')}).
          Modell: <strong>${esc(ki.model || 'noch keines gewählt')}</strong>.`
        : `Ollama ist nicht erreichbar. ${esc(ki.error || '')}`}
      </div>
      <p class="muted" style="margin-top:12px">Zwei Modelle, zwei Aufgaben: Das eine
        wertet Texte aus, das andere liest eine Kampflage und leitet daraus eine
        Entscheidung ab. Jede Suche prüft genau ihre Aufgabe.</p>
      <div class="form-actions">
        <button class="btn" id="kiModelle">Modelle anzeigen</button>
        <button class="btn primary" id="kiFinden">Modell für die Auswertung suchen</button>
        <button class="btn primary" id="kiFindenKampf">Modell für den Testlauf suchen</button>
      </div>
      <div id="kiOut" style="margin-top:14px"></div>
    </div>

    ${Object.entries(gruppen).map(([gruppe, felder]) => `
      <div class="panel">
        <div class="panel-head"><h3>${esc(gruppe)}</h3></div>
        ${gruppe === 'Kämpfe' ? mitschriftStand(mitschrift) : ''}
        <div style="display:grid;gap:18px">
          ${felder.map((f) => feldHtml(f, modelle)).join('')}
        </div>
      </div>`).join('')}

    <div class="panel" id="versionenPanel">
      <div class="panel-head"><h3>Gegner-Versionen</h3>
        <p class="muted">Wie schwer soll der Bot sein? Eine Version ist ein benannter
          Satz Einstellungen. „Standard" ist fest eingebaut und spielt so gut er kann.</p>
      </div>
      <div id="versionenListe"></div>
    </div>

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

  /* Beide Finder arbeiten gleich - nur Aufgabe und Zieleinstellung sind andere. */
  const sucheModell = async (art) => {
    const kampf = art === 'kampf';
    const box = $('#kiOut', ziel);
    box.innerHTML = `<div class="notice">Mehrere Modelle werden gleichzeitig geprüft
      ${kampf ? '— jedes muss drei Kampflagen richtig einschätzen' : 'mit einer kleinen Testaufgabe'}.
      Das kann ein bis zwei Minuten dauern …</div><div class="skeleton"></div>`;
    try {
      const r = await api('/api/ai/find-model', { json: { timeout: 90, art } });
      const schluessel = kampf ? 'ollama.model_kampf' : 'ollama.model';
      box.innerHTML = `
        ${r.recommended ? `<div class="notice ok">Empfehlung für
          ${kampf ? 'den Testlauf' : 'die Auswertung'}: <strong>${esc(r.recommended)}</strong>
          — ${kampf ? 'trifft die Entscheidungen und ist dabei am schnellsten'
                    : 'schnellstes Modell, das die Testaufgabe richtig löst'}.
          <button class="btn sm" id="kiUebernehmen" style="margin-left:10px">Übernehmen</button></div>`
        : '<div class="notice warn">Kein Modell hat die Aufgabe bestanden.</div>'}
        <div class="table-wrap" style="margin-top:12px"><table>
          <thead><tr><th>Modell</th><th>Ergebnis</th><th class="num">Dauer</th><th>Antwort</th></tr></thead><tbody>
          ${r.tested.map((t) => `<tr><td class="mono">${esc(t.model)}</td>
            <td>${t.ok ? '<span class="tag ok">geeignet</span>' : '<span class="tag bad">ungeeignet</span>'}
              ${t.von ? `<span class="muted"> ${t.treffer}/${t.von}</span>` : ''}</td>
            <td class="num">${t.seconds ? `${t.seconds} s` : '—'}</td>
            <td class="muted">${esc((t.answer || t.error || '').slice(0, 70))}</td></tr>`).join('')}
        </tbody></table></div>`;
      const uebernehmen = $('#kiUebernehmen', box);
      if (uebernehmen) {
        uebernehmen.addEventListener('click', async () => {
          try {
            await api('/api/settings', { json: { changes: { [schluessel]: r.recommended } } });
            toast(`Modell „${r.recommended}“ übernommen.`, 'ok');
            zeichne('einstellungen');
          } catch (e) { fehler(e); }
        });
      }
    } catch (e) { box.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
  };
  $('#kiFinden', ziel).addEventListener('click', () => sucheModell('verstaendnis'));
  $('#kiFindenKampf', ziel).addEventListener('click', () => sucheModell('kampf'));

  zeichneVersionen(ziel);

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

function feldHtml(f, modelle) {
  const gemeinsam = `data-key="${esc(f.key)}"`;
  let eingabe;
  if (f.key === 'ollama.model' && modelle && modelle.length) {
    // Auswahl statt Abtippen: die Namen kommen direkt von Ollama, damit hier
    // kein Modell landen kann, das gar nicht installiert ist.
    const bekannt = modelle.some((m) => m.name === f.value);
    eingabe = `<select ${gemeinsam}>
      <option value="">— keines (KI-Auswertung aus) —</option>
      ${modelle.map((m) => `<option value="${esc(m.name)}" ${m.name === f.value ? 'selected' : ''}
        >${esc(m.name)}${m.parameter_size ? ` · ${esc(m.parameter_size)}` : ''}</option>`).join('')}
      ${f.value && !bekannt
        ? `<option value="${esc(f.value)}" selected>${esc(f.value)} (nicht installiert)</option>` : ''}
    </select>`;
  } else if (f.type === 'bool') {
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
  waermeNamenAuf();
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
  zeigeVersion();
  STATE.auth = await api('/api/auth/status');
  await ladeServer();
  aktualisiereKopf();
  const ausHash = (location.hash.match(/^#\/(\w+)/) || [])[1];
  gehZu(ausHash && TABS[ausHash] ? ausHash : 'uebersicht');
  setInterval(aktualisiereKopf, 30000);
}

/* Ist a aelter als b? Zahlenweise, damit 1.10 nicht vor 1.9 landet. */
function versionKleiner(a, b) {
  const teile = (v) => String(v).split('.').map((n) => Number(n) || 0);
  const [x, y] = [teile(a), teile(b)];
  for (let i = 0; i < Math.max(x.length, y.length); i += 1) {
    if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) < (y[i] || 0);
  }
  return false;
}

async function zeigeVersion() {
  // Die Backend-Version kommt per API, die der Oberflaeche steckt oben in
  // dieser Datei. Weichen sie ab, wurde nur eines der drei Teile
  // aktualisiert - und genau dieser Fall sieht aus wie ein Programmfehler,
  // ist aber keiner: Knoepfe fehlen, Bereiche sind nicht da, und man sucht
  // den Fehler im Code statt beim Hochladen.
  const feld = $('#versionLabel');
  if (!feld) return;
  try {
    const info = await api('/api/health');
    const backend = (info && info.version) || '';
    if (!backend) return;
    if (backend === OBERFLAECHE_VERSION) {
      feld.textContent = `Web v${backend}`;
      return;
    }
    feld.textContent = `Oberfläche v${OBERFLAECHE_VERSION} · Backend v${backend}`;
    feld.classList.add('version-ungleich');
    const balken = document.createElement('div');
    balken.className = 'version-warnung';
    balken.innerHTML = `<strong>Die drei Teile passen nicht zusammen.</strong>
      Die Oberfläche ist <strong>v${esc(OBERFLAECHE_VERSION)}</strong>, das Backend
      <strong>v${esc(backend)}</strong>. Dadurch fehlen Knöpfe und ganze Bereiche —
      das ist kein Fehler im Programm.<br>
      ${versionKleiner(OBERFLAECHE_VERSION, backend)
        ? 'Lade <code>web/static</code> als ZIP in WebHafen hoch (Ordner vorher leeren) und drücke Strg+F5.'
        : 'Aktualisiere den Stack in Portainer (kartenbot-web → Update the stack).'}`;
    document.body.prepend(balken);
  } catch {
    /* Ohne Verbindung bleibt schlicht "Web" stehen - kein Grund fuer Laerm. */
  }
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
