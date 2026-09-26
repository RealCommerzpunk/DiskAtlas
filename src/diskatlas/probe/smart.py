"""SMART-Auslesen über smartctl (smartmontools) mit JSON-Ausgabe."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from diskatlas.probe.types import SmartInfo, clean

log = logging.getLogger(__name__)

WINDOWS_SMARTCTL = Path(r"C:\Program Files\smartmontools\bin\smartctl.exe")
PERMISSION_HINTS = ("permission denied", "operation not permitted", "access is denied")

# Bits im Exit-Code von smartctl (siehe `man smartctl`, Abschnitt RETURN VALUES)
RC_OPEN_FAILED = 0x02
RC_DISK_FAILING = 0x08
RC_PREFAIL_BELOW_THRESHOLD = 0x10
RC_PAST_BELOW_THRESHOLD = 0x20
RC_SELFTEST_ERRORS = 0x80


def find_smartctl(configured: str = "smartctl") -> str | None:
    found = shutil.which(configured)
    if found:
        return found
    if sys.platform == "win32" and WINDOWS_SMARTCTL.is_file():
        return str(WINDOWS_SMARTCTL)
    return None


def read_smart(device: str, smartctl: str = "smartctl", use_sudo: bool = True) -> SmartInfo:
    exe = find_smartctl(smartctl)
    if not exe:
        return SmartInfo(error="smartctl nicht gefunden – bitte smartmontools installieren")

    prefix: list[str] = []
    rc, data, stderr = _run([exe, "--json", "--all", device])
    if _permission_problem(rc, data, stderr, check_privileges=True) and use_sudo and _can_sudo():
        prefix = ["sudo", "-n"]
        rc, data, stderr = _run([*prefix, exe, "--json", "--all", device])
        if data is None:  # sudo verweigert (Passwort nötig) – smartctl lief gar nicht
            return SmartInfo(
                error="Keine Berechtigung: smartctl benötigt root-Rechte "
                "(siehe README, Abschnitt SMART-Berechtigungen)"
            )
    if data is not None and rc & RC_OPEN_FAILED and not _permission_problem(rc, data, stderr):
        # z. B. „Unknown USB bridge“: SAT-Durchreichung explizit versuchen
        rc2, data2, _ = _run([*prefix, exe, "--json", "--all", "-d", "sat", device])
        if data2 is not None and not rc2 & RC_OPEN_FAILED:
            rc, data = rc2, data2
    if data is None:
        return SmartInfo(error=f"smartctl lieferte keine Daten (rc={rc}): {stderr.strip()[:300]}")
    return parse_smartctl(data, rc)


def parse_smartctl(data: dict, rc: int = 0) -> SmartInfo:
    passed = (data.get("smart_status") or {}).get("passed")
    temperature = (data.get("temperature") or {}).get("current")
    power_on_hours = (data.get("power_on_time") or {}).get("hours")
    power_cycles = data.get("power_cycle_count")

    attrs = {a.get("id"): a for a in (data.get("ata_smart_attributes") or {}).get("table", [])}

    def raw(attr_id: int) -> int | None:
        attr = attrs.get(attr_id)
        value = (attr or {}).get("raw", {}).get("value")
        return int(value) if isinstance(value, int | float) else None

    reallocated, pending, uncorrectable = raw(5), raw(197), raw(198)

    nvme = data.get("nvme_smart_health_information_log") or {}
    percentage_used = nvme.get("percentage_used")
    media_errors = nvme.get("media_errors")
    critical_warning = nvme.get("critical_warning")
    if nvme:
        if temperature is None:
            temperature = nvme.get("temperature")
        if power_on_hours is None:
            power_on_hours = nvme.get("power_on_hours")
        if power_cycles is None:
            power_cycles = nvme.get("power_cycles")

    available = passed is not None or bool(attrs) or bool(nvme)

    if passed is False or rc & RC_DISK_FAILING:
        health = "failed"
    elif (
        any(v for v in (reallocated, pending, uncorrectable, media_errors) if v)
        or critical_warning
        or (percentage_used is not None and percentage_used >= 90)
        or rc & (RC_PREFAIL_BELOW_THRESHOLD | RC_PAST_BELOW_THRESHOLD | RC_SELFTEST_ERRORS)
    ):
        health = "warning"
    elif passed is True:
        health = "ok"
    else:
        health = "unknown"

    error = None
    if not available:
        messages = [m.get("string", "") for m in (data.get("smartctl") or {}).get("messages", [])]
        error = "; ".join(m for m in messages if m) or "SMART-Daten nicht verfügbar"

    return SmartInfo(
        available=available,
        health=health if available else "unknown",
        passed=passed,
        temperature_c=temperature,
        power_on_hours=power_on_hours,
        power_cycles=power_cycles,
        reallocated_sectors=reallocated,
        pending_sectors=pending,
        uncorrectable_sectors=uncorrectable,
        percentage_used=percentage_used,
        serial=clean(data.get("serial_number")),
        model=clean(data.get("model_name")),
        error=error,
        raw=data if available else None,
    )


def health_reasons(data: dict, rc: int | None = None) -> list[str]:
    """Erklärt in verständlichem Deutsch, warum die Bewertung nicht „Gut“ ist.

    Spiegelt die Regeln aus `parse_smartctl`; `rc` ist der smartctl-Exit-Code (Standard: der in
    den Rohdaten gespeicherte). Leere Liste = keine Auffälligkeit gefunden.
    """
    if rc is None:
        rc = int((data.get("smartctl") or {}).get("exit_status") or 0)
    reasons: list[str] = []
    if (data.get("smart_status") or {}).get("passed") is False:
        reasons.append("Die SMART-Gesamtbewertung der Platte lautet „FAILED“ (Ausfall droht).")
    if rc & RC_DISK_FAILING:
        reasons.append("smartctl meldet: Platte fällt aus bzw. ein Vorfehler-Wert ist kritisch.")

    attrs = {a.get("id"): a for a in (data.get("ata_smart_attributes") or {}).get("table", [])}
    for attr_id, text in (
        (5, "Wiederzugewiesene Sektoren"),
        (197, "Schwebende Sektoren"),
        (198, "Nicht korrigierbare Sektoren"),
    ):
        value = ((attrs.get(attr_id) or {}).get("raw") or {}).get("value")
        if isinstance(value, int | float) and value:
            reasons.append(f"{text}: {int(value)} (Attribut {attr_id}, sollte 0 sein).")

    nvme = data.get("nvme_smart_health_information_log") or {}
    if nvme.get("media_errors"):
        reasons.append(f"NVMe-Medienfehler: {nvme['media_errors']}.")
    if nvme.get("critical_warning"):
        reasons.append(f"NVMe meldet eine kritische Warnung (Wert {nvme['critical_warning']}).")
    if (nvme.get("percentage_used") or 0) >= 90:
        reasons.append(f"NVMe-Verschleiß bei {nvme['percentage_used']} %.")

    if rc & RC_PREFAIL_BELOW_THRESHOLD:
        reasons.append("Ein Vorfehler-Attribut liegt aktuell unter seinem Schwellenwert.")
    if rc & RC_PAST_BELOW_THRESHOLD:
        reasons.append("Ein Attribut lag in der Vergangenheit unter seinem Schwellenwert.")
    if rc & RC_SELFTEST_ERRORS:
        now_hours = (data.get("power_on_time") or {}).get("hours")
        table = (data.get("ata_smart_self_test_log") or {}).get("standard", {}).get("table", [])
        failed = [t for t in table if (t.get("status") or {}).get("passed") is False]
        for test in failed:
            hours = test.get("lifetime_hours")
            when = f"bei Betriebsstunde {hours:,}".replace(",", ".") if hours is not None else ""
            ago = (
                f" (vor {now_hours - hours:,} Betriebsstunden)".replace(",", ".")
                if hours is not None and now_hours is not None and now_hours >= hours
                else ""
            )
            lba = f", erster fehlerhafter Sektor (LBA) {test['lba']}" if test.get("lba") else ""
            reasons.append(
                f"Selbsttest „{(test.get('type') or {}).get('string', '?')}“ {when}{ago} "
                f"fehlgeschlagen: {(test.get('status') or {}).get('string', '?')}{lba}."
            )
        if not failed:
            reasons.append("Das Selbsttest-Protokoll der Platte enthält Fehler.")
    return reasons


def _run(cmd: list[str]) -> tuple[int, dict | None, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, None, str(exc)
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        data = None
    return proc.returncode, data, proc.stderr or ""


def _permission_problem(
    rc: int, data: dict | None, stderr: str, check_privileges: bool = False
) -> bool:
    texts = [stderr.lower()]
    if data:
        texts += [m.get("string", "").lower() for m in data.get("smartctl", {}).get("messages", [])]
    if any(hint in t for t in texts for hint in PERMISSION_HINTS):
        return True
    # Ohne root schlägt das Öffnen fast immer wegen fehlender Rechte fehl.
    return check_privileges and bool(rc & RC_OPEN_FAILED) and _is_unprivileged()


def _is_unprivileged() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() != 0


def _can_sudo() -> bool:
    return _is_unprivileged() and shutil.which("sudo") is not None
