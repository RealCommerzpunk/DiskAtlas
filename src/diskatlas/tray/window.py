"""Kleines Einstellungsfenster (pywebview): Verbindungsstatus anzeigen, Konfiguration bearbeiten."""

from __future__ import annotations

import logging
import os
import sys
import webbrowser
from dataclasses import asdict
from pathlib import Path

from diskatlas.probe.smart import bundled_smartctl
from diskatlas.tray import autostart, settings
from diskatlas.tray.status import age_text, read_status

log = logging.getLogger("diskatlas.tray")

HTML = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title>DiskAtlas Agent</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--fg:#1c2330;--muted:#667085;--line:#d8dde6;--accent:#2563eb;
--ok:#16a34a;--warn:#d97706;--bad:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#141922;--card:#1d2431;--fg:#e6eaf2;--muted:#98a2b3;
--line:#2f3848;--accent:#5b8def}}
*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;
margin-bottom:12px}
h2{margin:0 0 10px;font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.state{display:flex;align-items:center;gap:10px;font-size:16px;font-weight:600}
.dot{width:14px;height:14px;border-radius:50%;background:var(--muted);flex:none}
.dot.online{background:var(--ok)}.dot.offline,.dot.auth_error,.dot.error,.dot.config_error,
.dot.stopped{background:var(--bad)}.dot.starting,.dot.unconfigured{background:var(--warn)}
.meta{color:var(--muted);margin-top:6px;word-break:break-all}
.hint{margin-top:8px;color:var(--warn)}.hint:empty{display:none}
a{color:var(--accent);cursor:pointer}
label{display:block;margin:10px 0 3px;font-weight:600}
input[type=text],input[type=password],input[type=number]{width:100%;padding:7px 9px;
border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
.row{display:flex;gap:8px}.row input{flex:1}
.check{display:flex;align-items:center;gap:8px;margin:8px 0;font-weight:400}
.check input{margin:0}
button{padding:7px 14px;border:1px solid var(--line);border-radius:6px;background:var(--card);
color:var(--fg);font:inherit;cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button:disabled{opacity:.6;cursor:default}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
#msg{margin-top:10px;min-height:1.4em}#msg.ok{color:var(--ok)}#msg.bad{color:var(--bad)}
details summary{cursor:pointer;color:var(--muted);margin-top:10px}
small{color:var(--muted)}
</style></head><body>
<div class="card"><h2>Verbindung</h2>
<div class="state"><span id="dot" class="dot"></span><span id="label">…</span></div>
<div class="meta" id="detail"></div><div class="meta" id="facts"></div>
<div class="hint" id="hint"></div></div>

<div class="card"><h2>Einstellungen</h2>
<label class="check"><input type="radio" name="mode" id="mode_server" value="server">
Mit DiskAtlas-Server verbinden (z. B. Docker auf dem NAS)</label>
<label class="check"><input type="radio" name="mode" id="mode_local" value="local">
Nur dieser PC – Oberfläche und Datenbank laufen lokal</label>
<div id="server_fields">
<label for="server_url">Server-Adresse</label>
<input type="text" id="server_url" placeholder="http://unraid:8765" spellcheck="false">
<label for="api_token">API-Token</label>
<div class="row"><input type="password" id="api_token" spellcheck="false" autocomplete="off">
<button type="button" id="toggle">Zeigen</button></div></div>
<label for="host_name">Rechnername (leer = automatisch)</label>
<input type="text" id="host_name" spellcheck="false">
<label class="check"><input type="checkbox" id="autostart">
Beim Anmelden automatisch starten</label>
<label class="check"><input type="checkbox" id="index_files"> Dateien indizieren</label>
<label class="check"><input type="checkbox" id="auto_mount">
Datenträger automatisch einhängen (Linux)</label>
<details><summary>Erweitert</summary>
<label class="check"><input type="checkbox" id="index_system_volumes">
Systemvolumes indizieren</label>
<label class="check"><input type="checkbox" id="smart_use_sudo">
SMART mit sudo lesen (Linux)</label>
<label for="smartctl_path">Pfad zu smartctl</label>
<input type="text" id="smartctl_path" spellcheck="false">
<label for="poll_interval">Festplatten prüfen alle (Sekunden)</label>
<input type="number" id="poll_interval" min="1" step="1">
<label for="smart_interval_minutes">SMART auslesen alle (Minuten)</label>
<input type="number" id="smart_interval_minutes" min="1" step="1">
<label for="rescan_interval_hours">Dateiindex erneuern alle (Stunden)</label>
<input type="number" id="rescan_interval_hours" min="1" step="1"></details>
<div class="actions">
<button type="button" id="test">Verbindung testen</button>
<button type="button" class="primary" id="save">Speichern</button>
<button type="button" id="dash">Dashboard öffnen</button></div>
<div id="msg"></div>
<p><small id="path"></small></p><p><small id="notice"></small></p></div>

<script>
const TEXT = ["server_url","api_token","host_name","smartctl_path"];
const NUM = ["poll_interval","smart_interval_minutes","rescan_interval_hours"];
const BOOL = ["index_files","auto_mount","index_system_volumes","smart_use_sudo"];
const $ = id => document.getElementById(id);
function say(text, ok){const m=$("msg");m.textContent=text;m.className=ok?"ok":"bad";}
function fill(v){TEXT.forEach(k=>$(k).value=v[k]??"");NUM.forEach(k=>$(k).value=v[k]);
BOOL.forEach(k=>$(k).checked=!!v[k]);}
let current={};
const local=()=>$("mode_local").checked;
function showMode(){$("server_fields").hidden=local();$("test").hidden=local();}
function collect(){const v={};TEXT.forEach(k=>v[k]=$(k).value);
NUM.forEach(k=>v[k]=$(k).value);BOOL.forEach(k=>v[k]=$(k).checked);
if(local())v.server_url="";return v;}
function render(s){current=s;$("dot").className="dot "+s.state;$("label").textContent=s.label;
$("detail").textContent=s.detail;const f=[];
if(s.server_url)f.push((s.mode==="local"?"Oberfläche: ":"Server: ")+s.server_url);
if(s.host)f.push("Rechner: "+s.host);
if(s.state!=="stopped"){f.push("Letzte erfolgreiche Antwort: "+s.last_ok_ago);
f.push("Angeschlossene Datenträger: "+s.disks);}
$("facts").textContent=f.join("  ·  ");}
async function busy(id,fn){const b=$(id);b.disabled=true;try{await fn();}finally{b.disabled=false;}}
window.addEventListener("pywebviewready",async()=>{
const s=await pywebview.api.get_state();fill(s.values);render(s.status);
$("path").textContent="Konfigurationsdatei: "+s.config_path;
$((s.configured&&!s.values.server_url)?"mode_local":"mode_server").checked=true;showMode();
document.getElementsByName("mode").forEach(r=>r.onchange=showMode);
if(s.admin===false)$("hint").textContent="Ohne Administratorrechte liefert Windows keine "+
"SMART-Werte. Wie der Agent mit Adminrechten startet, steht in docs/AGENT.md.";
if(s.smartctl_bundled){$("notice").innerHTML="Enthält smartctl aus smartmontools (GNU GPL v2) – "+
"<a id='lic'>Lizenz und Quellcode</a>";$("lic").onclick=()=>pywebview.api.show_licenses();}
$("autostart").checked=s.autostart;
$("autostart").onchange=async e=>{const r=await pywebview.api.set_autostart(e.target.checked);
say(r.message,r.ok);if(!r.ok)e.target.checked=!e.target.checked;};
setInterval(async()=>render(await pywebview.api.get_status()),2000);
$("toggle").onclick=()=>{const p=$("api_token");p.type=p.type==="password"?"text":"password";
$("toggle").textContent=p.type==="password"?"Zeigen":"Verbergen";};
$("test").onclick=()=>busy("test",async()=>{say("Prüfe …",true);
const r=await pywebview.api.check_connection($("server_url").value,$("api_token").value);
say(r.message,r.ok);});
$("save").onclick=()=>busy("save",async()=>{const r=await pywebview.api.save(collect());
say(r.message,r.ok);});
$("dash").onclick=()=>pywebview.api.open_dashboard(
local()?(current.mode==="local"?current.server_url:""):$("server_url").value);
});
</script></body></html>"""


class SettingsApi:
    """Wird von JavaScript aufgerufen (pywebview: alle öffentlichen Methoden)."""

    def __init__(self, config_path: Path):
        self._config_path = Path(config_path)

    def get_state(self) -> dict:
        try:
            values = settings.load_values(self._config_path)
        except ValueError as exc:
            log.error("%s", exc)
            values = settings.default_values()
        return {
            "values": values,
            "status": self.get_status(),
            "config_path": str(self._config_path),
            "configured": self._config_path.is_file(),
            "autostart": autostart.is_enabled(),
            "admin": windows_admin(),
            "smartctl_bundled": bundled_smartctl() is not None,
        }

    def get_status(self) -> dict:
        status = read_status()
        data = asdict(status)
        data["label"] = status.label
        data["last_ok_ago"] = age_text(status.last_ok)
        return data

    def save(self, values: dict) -> dict:
        try:
            settings.save(self._config_path, settings.validate(values))
        except (ValueError, OSError) as exc:
            return {"ok": False, "message": str(exc)}
        return {
            "ok": True,
            "message": "Gespeichert. Der Agent übernimmt die Einstellungen in wenigen Sekunden.",
        }

    def set_autostart(self, enabled: bool) -> dict:
        try:
            autostart.set_enabled(bool(enabled))
        except OSError as exc:
            return {"ok": False, "message": f"Autostart nicht änderbar: {exc}"}
        if enabled:
            return {"ok": True, "message": "Der Agent startet ab jetzt bei der Anmeldung."}
        return {"ok": True, "message": "Autostart ist aus."}

    def check_connection(self, server_url: str, token: str) -> dict:
        ok, message = settings.check_connection(server_url, token)
        return {"ok": ok, "message": message}

    def open_dashboard(self, server_url: str) -> None:
        if server_url.startswith(("http://", "https://")):
            webbrowser.open(server_url)

    def show_licenses(self) -> None:
        exe = bundled_smartctl()
        if exe is not None:
            os.startfile(exe.parent / "LIESMICH.txt")


def windows_admin() -> bool | None:
    """Läuft das Programm unter Windows mit Administratorrechten? (None auf anderen Systemen)"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return None


def run_settings_window(config_path: Path) -> int:
    try:
        import webview
    except ImportError:
        log.error("pywebview fehlt. Installation: pip install -e \".[tray]\"")
        return 2
    webview.create_window(
        "DiskAtlas Agent", html=HTML, js_api=SettingsApi(config_path),
        width=560, height=760, min_size=(460, 520),
    )
    icon = Path(__file__).resolve().parent.parent / "web" / "static" / "icon_256.png"
    webview.start(icon=str(icon) if icon.is_file() else None)
    return 0
