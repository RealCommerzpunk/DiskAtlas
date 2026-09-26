// Barcode-Scanner für die iPhone-Web-App: Seriennummer scannen -> Platte und Ort anzeigen.
// Die Erkennung läuft komplett im Browser (ZXing, lokal ausgeliefert); es geht nur der
// erkannte Text an den Server.
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var video = $("scan-video"), view = $("scan-view"), toggle = $("scan-toggle");
  var statusEl = $("scan-status"), form = $("scan-form"), input = $("scan-input");
  var results = $("scan-results");
  var reader = null, active = false, lastCode = "", lastAt = 0;

  function setStatus(text) { statusEl.textContent = text || ""; }

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function formatSize(bytes) {
    if (!bytes) { return ""; }
    var tb = bytes / 1e12;
    return tb >= 1 ? tb.toLocaleString("de-DE", { maximumFractionDigits: 1 }) + " TB"
                   : Math.round(bytes / 1e9).toLocaleString("de-DE") + " GB";
  }

  function whereText(item) {
    if (item.state === "bay") {
      return "Steckt in Schacht " + item.bay + (item.host ? " (" + item.host + ")" : "");
    }
    if (item.state === "connected") {
      return "Angeschlossen" + (item.host ? " an " + item.host : "") + " (kein Schacht)";
    }
    return item.location ? "Lagerort: " + item.location : "Nicht angeschlossen – Lagerort unbekannt";
  }

  function saveLocation(item, field, note) {
    note.textContent = "Speichere …";
    fetch("/api/v1/disks/" + item.id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ location: field.value }),
    }).then(function (r) {
      if (!r.ok) { throw new Error(r.status); }
      note.textContent = "Gespeichert ✓";
    }).catch(function () { note.textContent = "Speichern fehlgeschlagen."; });
  }

  function renderItem(item) {
    var card = make("article", "scan-card scan-" + item.state);
    var title = make("a", "scan-title", item.name);
    title.href = "/disks/" + item.id;
    card.appendChild(title);
    var meta = [item.brand, item.model, formatSize(item.size_bytes)].filter(Boolean).join(" · ");
    if (meta) { card.appendChild(make("div", "muted", meta)); }
    if (item.serial) { card.appendChild(make("div", "muted small", "Seriennummer " + item.serial)); }
    card.appendChild(make("div", "scan-where", whereText(item)));

    var row = make("div", "scan-manual-row");
    var field = make("input");
    field.type = "text";
    field.value = item.location || "";
    field.placeholder = "Lagerort, z. B. Karton 3, Regal B";
    field.maxLength = 500;
    field.setAttribute("aria-label", "Lagerort");
    var save = make("button", "", "Lagerort speichern");
    save.type = "button";
    var note = make("div", "muted small");
    save.addEventListener("click", function () { saveLocation(item, field, note); });
    row.appendChild(field);
    row.appendChild(save);
    card.appendChild(row);
    card.appendChild(note);
    return card;
  }

  function lookup(code) {
    setStatus("Suche „" + code + "“ …");
    results.textContent = "";
    fetch("/api/v1/lookup?code=" + encodeURIComponent(code), { cache: "no-store" })
      .then(function (r) {
        if (r.status === 401) { window.location.reload(); throw new Error("login"); }
        if (!r.ok) { throw new Error(r.status); }
        return r.json();
      })
      .then(function (items) {
        if (!items.length) {
          setStatus("Keine Platte mit „" + code + "“ gefunden.");
          return;
        }
        setStatus(items.length === 1 ? "Gefunden." : items.length + " mögliche Platten:");
        items.forEach(function (item) { results.appendChild(renderItem(item)); });
      })
      .catch(function (e) {
        if (e.message !== "login") { setStatus("Suche fehlgeschlagen – Server erreichbar?"); }
      });
  }

  function onCode(code) {
    var now = Date.now();
    if (code === lastCode && now - lastAt < 4000) { return; }  // derselbe Barcode im Bild
    lastCode = code; lastAt = now;
    if (navigator.vibrate) { navigator.vibrate(60); }
    input.value = code;
    lookup(code);
  }

  function stop() {
    if (reader) { try { reader.reset(); } catch (e) { /* schon gestoppt */ } }
    active = false;
    view.hidden = true;
    toggle.textContent = "Kamera starten";
  }

  function cameraError(e) {
    if (e && (e.name === "NotAllowedError" || e.name === "SecurityError")) {
      return "Kein Zugriff auf die Kamera. In den iPhone-Einstellungen für Safari bzw. die Web-App erlauben.";
    }
    if (e && e.name === "NotFoundError") { return "Keine Kamera gefunden."; }
    return "Kamera konnte nicht gestartet werden.";
  }

  function start() {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("Die Kamera funktioniert nur über HTTPS (siehe docs/UNRAID.md, Tailscale). " +
                "Du kannst die Seriennummer unten eintippen.");
      return;
    }
    if (!window.ZXing) { setStatus("Scanner-Bibliothek nicht geladen."); return; }
    var Z = window.ZXing;
    var hints = new Map();
    hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, [
      Z.BarcodeFormat.CODE_128, Z.BarcodeFormat.CODE_39, Z.BarcodeFormat.CODE_93,
      Z.BarcodeFormat.DATA_MATRIX, Z.BarcodeFormat.QR_CODE, Z.BarcodeFormat.ITF,
      Z.BarcodeFormat.EAN_13, Z.BarcodeFormat.CODABAR,
    ]);
    hints.set(Z.DecodeHintType.TRY_HARDER, true);
    reader = new Z.BrowserMultiFormatReader(hints, 250);
    view.hidden = false;
    active = true;
    toggle.textContent = "Kamera stoppen";
    setStatus("Halte den Barcode ins Bild …");
    reader.decodeFromConstraints(
      { video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false },
      video,
      function (result) { if (result) { onCode(result.getText()); } }
    ).catch(function (e) { setStatus(cameraError(e)); stop(); });
  }

  toggle.addEventListener("click", function () { if (active) { stop(); } else { start(); } });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var code = input.value.trim();
    if (code.length < 4) { setStatus("Bitte mindestens 4 Zeichen eingeben."); return; }
    lookup(code);
  });
  document.addEventListener("visibilitychange", function () { if (document.hidden && active) { stop(); } });
})();
