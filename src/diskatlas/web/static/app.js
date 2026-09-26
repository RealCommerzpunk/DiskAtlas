// Kleine Progressive-Enhancement-Helfer – die Oberfläche funktioniert auch ohne JavaScript.
(function () {
  "use strict";

  // Sicherheitsabfrage für Formulare mit data-confirm
  document.addEventListener("submit", function (event) {
    var message = event.target.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  // Filterformulare bei Änderung sofort anwenden (Textsuche mit kurzer Verzögerung)
  document.querySelectorAll("form[data-autosubmit]").forEach(function (form) {
    var timer = null;
    form.addEventListener("change", function () { form.submit(); });
    form.querySelectorAll("input[type=search]").forEach(function (input) {
      input.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () { form.submit(); }, 450);
      });
      // Cursor nach dem Neuladen ans Ende setzen
      if (document.activeElement === input) {
        var len = input.value.length;
        input.setSelectionRange(len, len);
      }
    });
  });

  // Live-Aktualisierung (Dashboard, Festplattendetails): Seite neu laden, sobald sich
  // Verbindungsstatus, Gesundheit, Belegung, Dateianzahl oder die Belegung der Hot-Swap-Schächte
  // ändert. Nicht neu geladen wird nur, solange der Benutzer selbst etwas eingegeben hat
  // (ungespeicherte Notiz, halb getippte Suche); Fokus allein zählt nicht.
  if (document.body.hasAttribute("data-live") && window.fetch) {
    var known = null;
    var userEdited = false;
    var markEdited = function (event) {
      if (event.isTrusted && event.target.closest && event.target.closest("main")) { userEdited = true; }
    };
    document.addEventListener("input", markEdited);
    document.addEventListener("change", markEdited);

    var getJson = function (url) {
      return fetch(url, { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; });
    };
    var check = function () {
      Promise.all([
        getJson("/api/v1/disks"), getJson("/api/v1/bays/live"), getJson("/api/v1/commands/recent"),
      ]).then(function (res) {
        var disks = res[0], bays = res[1], cmds = res[2];
        if (!disks) { return; }  // Server kurz nicht erreichbar – beim nächsten Tick erneut
        var sig = JSON.stringify([
          disks.map(function (d) {
            return [d.id, d.is_connected, d.health, d.used_bytes, d.file_count, d.custom_name];
          }),
          bays ? bays.occupied.map(function (o) { return [o.port, o.serial]; }) : null,
          bays ? bays.online : null,
          cmds ? cmds.map(function (c) { return [c.id, c.status]; }) : null,
        ]);
        if (known !== null && sig !== known && !userEdited) {
          window.location.reload();
          return;
        }
        if (known === null) { known = sig; }
      });
    };
    check();
    setInterval(check, 3000);
  }

  // Warnbanner: laufende Indizierungen (Festplatte nicht abziehen), auf jeder Seite.
  var banner = document.getElementById("activity");
  if (banner && window.fetch) {
    var fmt = function (n) { return n.toLocaleString("de-DE"); };
    var renderActivity = function (items) {
      banner.textContent = "";
      if (!items.length) { banner.hidden = true; return; }
      items.forEach(function (it) {
        var line = document.createElement("div");
        var name = it.disk_name + (it.volume_label && it.volume_label !== it.disk_name ? " (" + it.volume_label + ")" : "");
        line.appendChild(document.createTextNode("\u26A0 Indizierung läuft: " + name + " – bitte nicht abziehen. "));
        var small = document.createElement("span");
        small.className = "small";
        small.textContent = fmt(it.files_so_far) + " Dateien bisher";
        line.appendChild(small);
        banner.appendChild(line);
      });
      banner.hidden = false;
    };
    var pollActivity = function () {
      fetch("/api/v1/activity", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (items) { if (items) { renderActivity(items); } })
        .catch(function () {});
    };
    pollActivity();
    setInterval(pollActivity, 3000);
  }
})();
