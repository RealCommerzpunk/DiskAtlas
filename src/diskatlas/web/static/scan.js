// DiskAtlas Scanner (iPhone-App): Barcodes auf Festplatten live erkennen.
// Modus „Info“ zeigt zur erkannten Platte eine Karte, Modus „Suchen“ markiert eine vorher gewählte
// Platte unübersehbar. Die Erkennung läuft komplett im Browser (BarcodeDetector, sonst ZXing);
// zum Server gehen nur Abfragen des Plattenverzeichnisses.
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var body = document.body, video = $("video"), canvas = $("marks"), ctx = canvas.getContext("2d");
  var sheet = $("sheet"), picker = $("picker"), toastEl = $("toast"), foundEl = $("found");
  var INDEX_KEY = "diskatlas.scan.index", TARGET_KEY = "diskatlas.scan.target";
  var REFRESH_MS = 5 * 60 * 1000, MARK_MS = 1600, SAME_CODE_MS = 2500;

  var state = {
    mode: body.dataset.mode === "find" ? "find" : "info",
    disks: [], target: null, shown: null, marks: [], seen: {},
    stream: null, reader: null, detector: null, running: false, audio: null,
  };

  // ------------------------------------------------------------------ Hilfen
  function make(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = text; }
    return n;
  }
  function norm(v) { return String(v || "").toUpperCase().replace(/[^A-Z0-9]/g, ""); }
  function size(b) {
    if (!b) { return ""; }
    var tb = b / 1e12;
    return tb >= 1 ? tb.toLocaleString("de-DE", { maximumFractionDigits: 1 }) + " TB"
                   : Math.round(b / 1e9).toLocaleString("de-DE") + " GB";
  }
  var toastTimer = null;
  function toast(text, ms) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, ms || 2200);
  }
  function whereText(d) {
    if (d.state === "bay") { return "Steckt in Schacht " + d.bay + (d.host ? " · " + d.host : ""); }
    if (d.state === "connected") { return "Angeschlossen" + (d.host ? " an " + d.host : ""); }
    return d.location ? "Lagerort: " + d.location : "Nicht angeschlossen · Lagerort unbekannt";
  }
  var HEALTH = { ok: "Gut", warning: "Warnung", failed: "Kritisch", unknown: "Unbekannt" };

  // ------------------------------------------------------------------ Plattenverzeichnis
  function loadCached() {
    try {
      var raw = localStorage.getItem(INDEX_KEY);
      if (raw) { state.disks = JSON.parse(raw).disks || []; }
    } catch (e) { state.disks = []; }
  }
  function refreshIndex() {
    return fetch("/api/v1/scan/index", { cache: "no-store", credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 401) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search); throw new Error("login"); }
        if (!r.ok) { throw new Error(r.status); }
        return r.json();
      })
      .then(function (data) {
        state.disks = data.disks || [];
        try { localStorage.setItem(INDEX_KEY, JSON.stringify(data)); } catch (e) { /* voll */ }
        restoreTarget();
      })
      .catch(function (e) {
        if (e.message !== "login" && !state.disks.length) { toast("Server nicht erreichbar – Verzeichnis fehlt.", 4000); }
      });
  }

  // Seriennummer oder WWN im Barcode finden (wie der Server: genau, sonst enthalten)
  function match(code) {
    var wanted = norm(code);
    if (wanted.length < 4) { return []; }
    var exact = [], partial = [];
    state.disks.forEach(function (d) {
      var cands = [norm(d.serial), norm(d.wwn)];
      for (var i = 0; i < cands.length; i++) {
        var c = cands[i];
        if (!c) { continue; }
        if (c === wanted) { exact.push(d); return; }
        if ((c.length >= 6 && wanted.indexOf(c) >= 0) || (wanted.length >= 6 && c.indexOf(wanted) >= 0)) {
          partial.push(d); return;
        }
      }
    });
    return exact.length ? exact : partial;
  }
  function byId(id) {
    for (var i = 0; i < state.disks.length; i++) { if (state.disks[i].id === id) { return state.disks[i]; } }
    return null;
  }

  // ------------------------------------------------------------------ Markierungen im Bild
  function resize() {
    var r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(r.width * dpr);
    canvas.height = Math.round(r.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  // Punkte im Kamerabild -> Bildschirm (Video füllt den Schirm mit „object-fit: cover“)
  function toScreen(points) {
    var vw = video.videoWidth, vh = video.videoHeight;
    var r = canvas.getBoundingClientRect();
    if (!vw || !vh || !points || !points.length) { return null; }
    var s = Math.max(r.width / vw, r.height / vh), ox = (r.width - vw * s) / 2, oy = (r.height - vh * s) / 2;
    var xs = points.map(function (p) { return p.x * s + ox; });
    var ys = points.map(function (p) { return p.y * s + oy; });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    var padX = 14, padY = Math.max(18, (x1 - x0) * 0.12);  // 1D-Codes liefern nur eine Linie
    return { x: x0 - padX, y: y0 - padY, w: x1 - x0 + 2 * padX, h: y1 - y0 + 2 * padY };
  }
  function addMark(points, kind, label) {
    var box = toScreen(points);
    if (!box) { return; }
    state.marks.push({ box: box, kind: kind, label: label, at: Date.now() });
  }
  function draw() {
    var r = canvas.getBoundingClientRect(), now = Date.now();
    ctx.clearRect(0, 0, r.width, r.height);
    state.marks = state.marks.filter(function (m) { return now - m.at < MARK_MS; });
    state.marks.forEach(function (m) {
      var a = 1 - (now - m.at) / MARK_MS, b = m.box;
      var color = m.kind === "hit" ? "34,197,94" : m.kind === "other" ? "239,68,68" : "255,255,255";
      ctx.lineWidth = m.kind === "hit" ? 6 : 4;
      ctx.strokeStyle = "rgba(" + color + "," + Math.min(1, a + 0.2) + ")";
      ctx.fillStyle = "rgba(" + color + "," + (m.kind === "hit" ? 0.22 : 0.12) * a + ")";
      roundRect(b.x, b.y, b.w, b.h, 12);
      ctx.fill();
      ctx.stroke();
      if (m.label) {
        ctx.font = "600 15px -apple-system, system-ui, sans-serif";
        var tw = ctx.measureText(m.label).width + 16, ty = Math.max(4, b.y - 30);
        ctx.fillStyle = "rgba(" + color + "," + Math.min(1, a + 0.3) + ")";
        roundRect(b.x, ty, tw, 26, 8);
        ctx.fill();
        ctx.fillStyle = m.kind === "hit" ? "#04210f" : "#fff";
        ctx.fillText(m.label, b.x + 8, ty + 18);
      }
    });
    requestAnimationFrame(draw);
  }
  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // Ton und Vibration als Bestätigung (iOS kennt kein Vibrieren im Browser: dann nur Ton)
  function unlockAudio() {
    if (state.audio || !(window.AudioContext || window.webkitAudioContext)) { return; }
    try { state.audio = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { state.audio = null; }
  }
  function beep(freq, ms) {
    if (navigator.vibrate) { navigator.vibrate(ms > 150 ? [80, 60, 80] : 50); }
    if (!state.audio) { return; }
    var o = state.audio.createOscillator(), g = state.audio.createGain();
    o.frequency.value = freq;
    g.gain.value = 0.12;
    o.connect(g);
    g.connect(state.audio.destination);
    o.start();
    o.stop(state.audio.currentTime + ms / 1000);
  }

  // ------------------------------------------------------------------ Erkannter Barcode
  function onCode(text, points) {
    var code = norm(text), now = Date.now();
    if (!code) { return; }
    state.lastCodeAt = now;
    var repeat = state.seen[code] && now - state.seen[code] < SAME_CODE_MS;
    state.seen[code] = now;
    var hits = match(text);
    if (state.mode === "find") { return onFind(hits, points, repeat); }
    if (hits.length) {
      addMark(points, "hit", hits[0].name);
      if (!repeat && (!state.shown || state.shown.id !== hits[0].id)) { beep(880, 90); showDisk(hits[0], hits.length); }
      return;
    }
    if (repeat) { return; }
    // Verzeichnis evtl. veraltet: einmal beim Server nachfragen
    fetch("/api/v1/lookup?code=" + encodeURIComponent(text), { cache: "no-store", credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (items) {
        if (items.length) {
          refreshIndex();
          addMark(points, "hit", items[0].name);
          showDisk(items[0], items.length);
        } else {
          addMark(points, "none", null);
          toast("Kein Treffer für „" + text.slice(0, 24) + "“ – evtl. ein anderer Barcode auf dem Etikett.");
        }
      })
      .catch(function () { addMark(points, "none", null); });
  }
  function onFind(hits, points, repeat) {
    if (!state.target) { return; }
    var isTarget = hits.some(function (d) { return d.id === state.target.id; });
    if (isTarget) {
      addMark(points, "hit", "GEFUNDEN");
      if (!repeat) {
        beep(1046, 260);
        foundEl.hidden = false;
        body.classList.remove("flash");
        void body.offsetWidth;  // Animation neu starten
        body.classList.add("flash");
        clearTimeout(onFind.timer);
        onFind.timer = setTimeout(function () { foundEl.hidden = true; body.classList.remove("flash"); }, 2500);
      }
    } else if (hits.length) {
      addMark(points, "other", "Nicht gesucht: " + hits[0].name);
    }
  }

  // ------------------------------------------------------------------ Info-Karte
  function showDisk(d, count) {
    state.shown = d;
    sheet.textContent = "";
    var head = make("div", "sheet-head");
    head.appendChild(make("h2", "", d.name));
    var close = make("button", "round small", "✕");
    close.type = "button";
    close.setAttribute("aria-label", "Schließen");
    close.addEventListener("click", hideSheet);
    head.appendChild(close);
    sheet.appendChild(head);
    var meta = [d.brand, d.model, size(d.size_bytes)].filter(Boolean).join(" · ");
    var metaEl = make("div", "meta", meta);
    if (d.serial) { metaEl.appendChild(document.createTextNode((meta ? " · " : "") + "S/N " + d.serial)); }
    sheet.appendChild(metaEl);
    sheet.appendChild(make("div", "where " + d.state, whereText(d)));
    var facts = make("div", "meta");
    facts.appendChild(make("span", "health " + d.health, HEALTH[d.health] || d.health));
    if (d.file_count) { facts.appendChild(document.createTextNode(" · " + d.file_count.toLocaleString("de-DE") + " Dateien")); }
    if (count > 1) { facts.appendChild(document.createTextNode(" · " + count + " mögliche Platten")); }
    sheet.appendChild(facts);
    if (d.labels && d.labels.length) {
      var chips = make("div", "chips");
      d.labels.forEach(function (l) { chips.appendChild(make("span", "chip", l)); });
      sheet.appendChild(chips);
    }
    var row = make("div", "row"), field = make("input"), save = make("button", "", "Lagerort speichern");
    field.type = "text"; field.value = d.location || ""; field.maxLength = 500;
    field.placeholder = "Lagerort, z. B. Karton 3, Regal B";
    field.setAttribute("aria-label", "Lagerort");
    save.type = "button";
    var note = make("div", "note");
    save.addEventListener("click", function () { saveLocation(d, field.value, note); });
    row.appendChild(field); row.appendChild(save);
    sheet.appendChild(row); sheet.appendChild(note);
    var actions = make("div", "actions");
    var details = make("a", "", "Details");
    details.href = "/disks/" + d.id;
    var hunt = make("button", "", "Diese Platte suchen");
    hunt.type = "button";
    hunt.addEventListener("click", function () { setTarget(byId(d.id) || d); setMode("find"); });
    actions.appendChild(details); actions.appendChild(hunt);
    sheet.appendChild(actions);
    picker.hidden = true;
    sheet.hidden = false;
  }
  function hideSheet() { sheet.hidden = true; state.shown = null; }
  function saveLocation(d, value, note) {
    note.textContent = "Speichere …";
    fetch("/api/v1/disks/" + d.id, {
      method: "PATCH", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ location: value }),
    }).then(function (r) {
      if (r.status === 403) { throw new Error("Nur der Besitzer kann den Lagerort ändern."); }
      if (!r.ok) { throw new Error("Speichern fehlgeschlagen."); }
      note.textContent = "Gespeichert ✓";
      d.location = value;
      var known = byId(d.id);
      if (known) { known.location = value; }
    }).catch(function (e) { note.textContent = e.message; });
  }

  // ------------------------------------------------------------------ Suchmodus
  function setTarget(d) {
    state.target = d;
    try { if (d) { localStorage.setItem(TARGET_KEY, String(d.id)); } } catch (e) { /* egal */ }
    renderTarget();
  }
  function restoreTarget() {
    if (state.target) { state.target = byId(state.target.id) || state.target; renderTarget(); return; }
    var id = parseInt(body.dataset.target || "", 10);
    if (!id) { try { id = parseInt(localStorage.getItem(TARGET_KEY) || "", 10); } catch (e) { id = 0; } }
    if (id && byId(id)) { setTarget(byId(id)); picker.hidden = true; }
    else if (state.mode === "find") { openPicker(); }
  }
  function renderTarget() {
    var show = state.mode === "find" && state.target;
    $("target").hidden = !show;
    body.classList.toggle("hunting", !!show);
    if (!show) { return; }
    $("target-name").textContent = state.target.name;
    $("target-meta").textContent = [state.target.serial ? "S/N " + state.target.serial : "", whereText(state.target)]
      .filter(Boolean).join(" · ");
  }
  function openPicker() {
    hideSheet();
    picker.hidden = false;
    renderPicker();
  }
  function renderPicker() {
    var q = norm($("picker-q").value), list = $("picker-list");
    list.textContent = "";
    var shown = state.disks.filter(function (d) {
      if (!q) { return true; }
      return [d.name, d.serial, d.model, d.location].concat(d.labels || []).some(function (v) { return norm(v).indexOf(q) >= 0; });
    }).slice(0, 80);
    if (!shown.length) { list.appendChild(make("li", "note", state.disks.length ? "Keine Platte passt." : "Verzeichnis wird geladen …")); }
    shown.forEach(function (d) {
      var li = make("li"), btn = make("button");
      btn.type = "button";
      btn.appendChild(make("span", "", d.name));
      btn.appendChild(make("small", "", [d.serial ? "S/N " + d.serial : "", whereText(d)].filter(Boolean).join(" · ")));
      btn.addEventListener("click", function () { unlockAudio(); setTarget(d); picker.hidden = true; toast("Halte die Kamera über die Etiketten …"); });
      li.appendChild(btn);
      list.appendChild(li);
    });
  }

  // ------------------------------------------------------------------ Modus
  function setMode(mode) {
    state.mode = mode;
    body.dataset.mode = mode;
    document.querySelectorAll(".modes button").forEach(function (b) {
      b.setAttribute("aria-selected", b.dataset.mode === mode ? "true" : "false");
    });
    var url = new URL(location.href);
    url.searchParams.set("mode", mode);
    if (mode === "find" && state.target) { url.searchParams.set("disk", state.target.id); } else { url.searchParams.delete("disk"); }
    history.replaceState(null, "", url.pathname + url.search);
    foundEl.hidden = true;
    if (mode === "find") { hideSheet(); if (!state.target) { openPicker(); } } else { picker.hidden = true; }
    renderTarget();
  }

  // ------------------------------------------------------------------ Kamera
  var FORMATS = ["code_128", "code_39", "code_93", "data_matrix", "qr_code", "itf", "ean_13", "codabar"];

  function cameraError(e) {
    if (e && (e.name === "NotAllowedError" || e.name === "SecurityError")) {
      return "Kein Kamerazugriff. In den iPhone-Einstellungen erlauben (Safari bzw. die App).";
    }
    if (e && e.name === "NotFoundError") { return "Keine Kamera gefunden."; }
    return "Kamera konnte nicht gestartet werden.";
  }
  function showStart(msg) {
    $("start-msg").textContent = msg;
    $("start").hidden = false;
  }
  function start() {
    $("start").hidden = true;
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showStart("Die Kamera funktioniert nur über HTTPS (z. B. Tailscale, siehe Anleitung). Seriennummer unten eintippen geht immer.");
      return;
    }
    // Hohe Auflösung: feine Strichcodes brauchen 2–3 scharfe Bildpunkte je Strich
    var constraints = { audio: false, video: { facingMode: { ideal: "environment" }, width: { ideal: 3840 }, height: { ideal: 2160 } } };
    state.running = true;
    // 1. eingebaute Erkennung (Chrome/Android), 2. zxing-cpp als WebAssembly (iPhone/Safari),
    // 3. notfalls die ältere ZXing-Bibliothek in JavaScript
    pickDetector().then(function (Detector) {
      return Detector.getSupportedFormats().then(function (supported) {
        var formats = FORMATS.filter(function (f) { return supported.indexOf(f) >= 0; });
        if (!formats.length) { throw new Error("keine Formate"); }
        state.detector = new Detector({ formats: formats });
        return navigator.mediaDevices.getUserMedia(constraints);
      });
    }).then(function (stream) {
      attach(stream);
      detectLoop();
    }).catch(function (e) {
      if (e && e.name && /NotAllowed|Security|NotFound/.test(e.name)) { state.running = false; showStart(cameraError(e)); return; }
      startZxing({ audio: false, video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } } });
    });
  }
  function pickDetector() {
    if ("BarcodeDetector" in window) {
      return window.BarcodeDetector.getSupportedFormats().then(function (f) {
        return f.length ? window.BarcodeDetector : wasmDetector();
      });
    }
    return wasmDetector();
  }
  function wasmDetector() {
    var api = window.BarcodeDetectionAPI;
    if (!api || !api.BarcodeDetector) { return Promise.reject(new Error("kein Detector")); }
    var version = body.dataset.version || "";
    api.prepareZXingModule({
      overrides: {
        locateFile: function (path, prefix) {
          return path.slice(-5) === ".wasm" ? "/static/vendor/" + path + "?v=" + version : prefix + path;
        },
      },
      fireImmediately: true,
    });
    return Promise.resolve(api.BarcodeDetector);
  }
  function startZxing(constraints) {
    if (!window.ZXing) { showStart("Scanner-Bibliothek nicht geladen."); state.running = false; return; }
    var Z = window.ZXing, hints = new Map();
    hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, [
      Z.BarcodeFormat.CODE_128, Z.BarcodeFormat.CODE_39, Z.BarcodeFormat.CODE_93, Z.BarcodeFormat.DATA_MATRIX,
      Z.BarcodeFormat.QR_CODE, Z.BarcodeFormat.ITF, Z.BarcodeFormat.EAN_13, Z.BarcodeFormat.CODABAR,
    ]);
    hints.set(Z.DecodeHintType.TRY_HARDER, true);
    state.reader = new Z.BrowserMultiFormatReader(hints, 120);
    state.reader.decodeFromConstraints(constraints, video, function (result) {
      if (!result) { return; }
      var pts = (result.getResultPoints() || []).map(function (p) { return { x: p.getX(), y: p.getY() }; });
      onCode(result.getText(), pts);
    }).then(function () {
      state.stream = video.srcObject;
      setupTorch();
    }).catch(function (e) { state.running = false; showStart(cameraError(e)); });
  }
  function attach(stream) {
    state.startedAt = Date.now();
    state.stream = stream;
    video.srcObject = stream;
    video.play().catch(function () { /* stumm: Wiedergabe startet trotzdem */ });
    setupTorch();
  }
  // Ausschnitt um den Rahmen in voller Kameraauflösung (Bildschirm -> Kamerabild umgerechnet)
  var crop = document.createElement("canvas"), cropCtx = crop.getContext("2d", { willReadFrequently: true });
  var frameNo = 0;
  function guideInVideo() {
    var vw = video.videoWidth, vh = video.videoHeight;
    var r = canvas.getBoundingClientRect(), g = document.querySelector(".guide").getBoundingClientRect();
    if (!vw || !vh || !r.width) { return null; }
    var s = Math.max(r.width / vw, r.height / vh), ox = (r.width - vw * s) / 2, oy = (r.height - vh * s) / 2;
    var extra = g.height * 0.6;  // etwas großzügiger als der sichtbare Rahmen
    var x = Math.max(0, (g.left - r.left - ox) / s), y = Math.max(0, (g.top - r.top - extra - oy) / s);
    var w = Math.min(vw - x, g.width / s), h = Math.min(vh - y, (g.height + 2 * extra) / s);
    return w > 50 && h > 30 ? { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) } : null;
  }
  function detectLoop() {
    if (!state.running || !state.detector) { return; }
    if (video.readyState < 2) { setTimeout(detectLoop, 200); return; }
    frameNo += 1;
    var area = frameNo % 4 === 0 ? null : guideInVideo();  // jedes 4. Bild: ganzes Bild
    var source = video, dx = 0, dy = 0;
    if (area) {
      crop.width = area.w;
      crop.height = area.h;
      cropCtx.drawImage(video, area.x, area.y, area.w, area.h, 0, 0, area.w, area.h);
      source = crop; dx = area.x; dy = area.y;
    }
    state.detector.detect(source).then(function (codes) {
      codes.forEach(function (c) {
        var pts = (c.cornerPoints || []).map(function (p) { return { x: p.x + dx, y: p.y + dy }; });
        onCode(c.rawValue, pts);
      });
    }).catch(function () { /* ein Bild verpasst */ }).then(function () { setTimeout(detectLoop, 60); });
  }
  // Hinweis, wenn eine Weile nichts erkannt wird
  var lastHint = 0;
  setInterval(function () {
    var now = Date.now(), last = Math.max(state.lastCodeAt || 0, state.startedAt || now);
    if (!state.running || !sheet.hidden || !picker.hidden || now - last < 6000 || now - lastHint < 9000) { return; }
    lastHint = now;
    toast("Kein Barcode erkannt – näher an ein Etikett, bis es den Rahmen ausfüllt" + (state.zoomable ? " (oder 2×)" : "") + ".", 3500);
  }, 1000);
  function stop() {
    state.running = false;
    if (state.reader) { try { state.reader.reset(); } catch (e) { /* schon gestoppt */ } state.reader = null; }
    if (state.stream) { state.stream.getTracks().forEach(function (t) { t.stop(); }); state.stream = null; }
    video.srcObject = null;
  }
  function setupTorch() {
    var track = state.stream && state.stream.getVideoTracks()[0];
    var caps = track && track.getCapabilities ? track.getCapabilities() : {};
    var btn = $("torch");
    btn.hidden = !caps.torch;
    btn.setAttribute("aria-pressed", "false");
    btn.onclick = function () {
      var on = btn.getAttribute("aria-pressed") !== "true";
      track.applyConstraints({ advanced: [{ torch: on }] }).then(function () {
        btn.setAttribute("aria-pressed", on ? "true" : "false");
      }).catch(function () { btn.hidden = true; });
    };
    // Zoom (wenn die Kamera ihn anbietet): näher heran, ohne das iPhone näher zu halten
    var zoomBtn = $("zoom");
    state.zoomable = !!(caps.zoom && caps.zoom.max >= 1.5);
    zoomBtn.hidden = !state.zoomable;
    zoomBtn.setAttribute("aria-pressed", "false");
    zoomBtn.onclick = function () {
      var on = zoomBtn.getAttribute("aria-pressed") !== "true";
      var level = on ? Math.min(2, caps.zoom.max) : Math.max(1, caps.zoom.min || 1);
      track.applyConstraints({ advanced: [{ zoom: level }] }).then(function () {
        zoomBtn.setAttribute("aria-pressed", on ? "true" : "false");
      }).catch(function () { zoomBtn.hidden = true; state.zoomable = false; });
    };
  }

  // ------------------------------------------------------------------ Start
  document.querySelectorAll(".modes button").forEach(function (b) {
    b.addEventListener("click", function () { unlockAudio(); setMode(b.dataset.mode); });
  });
  $("target-change").addEventListener("click", function () { unlockAudio(); openPicker(); });
  $("picker-close").addEventListener("click", function () { picker.hidden = true; if (!state.target) { setMode("info"); } });
  $("picker-q").addEventListener("input", renderPicker);
  $("start-btn").addEventListener("click", function () { unlockAudio(); start(); });
  $("manual-toggle").addEventListener("click", function () {
    var form = $("manual");
    form.hidden = !form.hidden;
    if (!form.hidden) { $("manual-input").focus(); }
  });
  $("manual").addEventListener("submit", function (e) {
    e.preventDefault();
    unlockAudio();
    var value = $("manual-input").value.trim();
    if (norm(value).length < 4) { toast("Bitte mindestens 4 Zeichen eingeben."); return; }
    state.seen = {};
    onCode(value, null);
    $("manual-input").blur();
  });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { stop(); } else if (!state.running) { start(); refreshIndex(); }
  });
  window.addEventListener("resize", resize);
  document.addEventListener("touchstart", unlockAudio, { once: true, passive: true });

  if ("serviceWorker" in navigator) { navigator.serviceWorker.register("/sw.js").catch(function () { /* optional */ }); }
  resize();
  requestAnimationFrame(draw);
  loadCached();
  setMode(state.mode);
  restoreTarget();
  var direct = new URL(location.href).searchParams.get("code");  // Direktlink /scan?code=…
  if (direct && state.mode === "info") { onCode(direct, null); }
  refreshIndex();
  setInterval(refreshIndex, REFRESH_MS);
  start();
})();
