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
          bays ? bays.occupied.map(function (o) { return [o.client_id, o.port, o.serial]; }) : null,
          bays ? bays.online : null,
          bays && bays.clients ? Object.keys(bays.clients).map(function (id) {
            return [id, bays.clients[id].online];
          }) : null,
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

  // Laufende Indizierungen: kompakte Anzeige in der Kopfleiste (Festplatte nicht abziehen).
  var banner = document.getElementById("activity");
  if (banner && window.fetch) {
    var fmt = function (n) { return n.toLocaleString("de-DE"); };
    var label = function (it) {
      return it.disk_name + (it.volume_label && it.volume_label !== it.disk_name ? " (" + it.volume_label + ")" : "");
    };
    var renderActivity = function (items) {
      banner.textContent = "";
      if (!items.length) { banner.hidden = true; return; }
      var first = items[0];
      var dot = document.createElement("span");
      dot.className = "pulse";
      dot.setAttribute("aria-hidden", "true");
      var text = document.createElement("span");
      text.className = "pill-text";
      text.textContent = "Indiziert: " + label(first);
      var count = document.createElement("span");
      count.className = "pill-count";
      count.textContent = items.length > 1 ? "+" + (items.length - 1) : fmt(first.files_so_far);
      banner.append(dot, text, count);
      banner.href = "/disks/" + first.disk_id;
      banner.title = items.map(function (it) {
        return "Indizierung läuft: " + label(it) + " – " + fmt(it.files_so_far) + " Dateien bisher";
      }).join("\n") + "\nBitte die Festplatte" + (items.length > 1 ? "n" : "") + " nicht abziehen.";
      banner.setAttribute("aria-label", banner.title);
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

// Benutzermenü: Esc oder Klick daneben schließt es.
(function () {
  const menu = document.querySelector(".usermenu");
  if (!menu) return;
  document.addEventListener("click", (e) => { if (!menu.contains(e.target)) menu.open = false; });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); }
  });
})();

// Dateiauswahl: Zähler, „alle“, Anfordern-Knopf erst bei Auswahl aktiv.
(function () {
  document.querySelectorAll("form[data-selection]").forEach((form) => {
    const boxes = () => Array.from(form.querySelectorAll('input[name="sel"]'));
    const update = () => {
      const n = boxes().filter((b) => b.checked).length;
      form.querySelector("[data-selected-count]").textContent = n;
      const button = form.querySelector("[data-request-button]");
      if (button && button.dataset.enabled) button.disabled = n === 0;
    };
    form.addEventListener("change", (e) => {
      if (e.target.matches("[data-select-all]")) boxes().forEach((b) => { b.checked = e.target.checked; });
      update();
    });
    update();
  });
})();
