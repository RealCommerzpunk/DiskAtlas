"""Ziele für Scan-Ergebnisse: direkt in die Datenbank oder per HTTP an einen DiskAtlas-Server."""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from diskatlas.db import Database
from diskatlas.probe.types import DiskInfo, FileRecord
from diskatlas.services import commands, hosts, ingest


class RelayGone(Exception):
    """Der Server kennt die Übertragung nicht mehr (abgebrochen, abgelaufen oder nicht deine)."""


class Sink(ABC):
    @abstractmethod
    def report_disk(self, host: str, disk: DiskInfo) -> bool:
        """Meldet die Platte; False = gehört einem anderen Benutzer (Übernahme beantragt)."""

    @abstractmethod
    def report_connected(
        self, host: str, disk_keys: list[str], ports_info: dict | None = None
    ) -> None: ...

    @abstractmethod
    def fetch_commands(self, host: str) -> list[dict]: ...

    @abstractmethod
    def report_command(self, command_id: int, ok: bool, message: str) -> None: ...

    @abstractmethod
    def begin_index(self, disk_key: str, volume_key: str) -> str: ...

    @abstractmethod
    def add_files(
        self, disk_key: str, volume_key: str, scan_id: str, records: list[FileRecord]
    ) -> None: ...

    @abstractmethod
    def finish_index(
        self, disk_key: str, volume_key: str, scan_id: str, errors: int, success: bool
    ) -> None: ...

    # Übertragungen („Datei anfordern“) gibt es nur gegenüber einem Server mit Clients.
    capabilities: dict = {}  # noqa: RUF012 - wird je Instanz gesetzt

    def fetch_transfers(self) -> list[dict]:
        return []

    def transfer_progress(self, item_id: str, bytes_done: int) -> bool:
        """False = abbrechen."""
        return True

    def transfer_done(  # noqa: B027
        self, item_id: str, sha256: str, size: int, name: str, message: str
    ) -> None:
        pass

    def transfer_fail(self, item_id: str, message: str, retry: bool) -> None:  # noqa: B027
        pass

    # Relay: verschlüsselte Stücke über den Server (siehe services/relay.py auf der Serverseite)
    def relay_put(self, item_id: str, n: int, data: bytes, epk: str | None, final: bool) -> str:
        raise NotImplementedError

    def relay_status(self, item_id: str) -> dict:
        raise NotImplementedError

    def relay_get(self, item_id: str, n: int) -> tuple[bytes, str, bool] | None:
        raise NotImplementedError

    def relay_ack(self, item_id: str, n: int) -> None:
        raise NotImplementedError

    def close(self) -> None:  # noqa: B027 - optionaler Hook
        pass


class DatabaseSink(Sink):
    def __init__(self, db: Database):
        self.db = db

    def report_disk(self, host: str, disk: DiskInfo) -> bool:
        with self.db.session() as s:
            ingest.upsert_disk(s, host, disk)
        return True

    def report_connected(
        self, host: str, disk_keys: list[str], ports_info: dict | None = None
    ) -> None:
        with self.db.session() as s:
            ingest.mark_connected(s, host, disk_keys)
            hosts.record(s, host, ports_info)

    def fetch_commands(self, host: str) -> list[dict]:
        with self.db.session() as s:
            return commands.claim_pending(s, host)

    def report_command(self, command_id: int, ok: bool, message: str) -> None:
        with self.db.session() as s:
            commands.finish(s, command_id, ok, message)

    def begin_index(self, disk_key: str, volume_key: str) -> str:
        with self.db.session() as s:
            return ingest.begin_index(s, disk_key, volume_key)

    def add_files(self, disk_key, volume_key, scan_id, records) -> None:
        with self.db.session() as s:
            ingest.add_files(s, disk_key, volume_key, scan_id, records)

    def finish_index(self, disk_key, volume_key, scan_id, errors, success) -> None:
        with self.db.session() as s:
            ingest.finish_index(s, disk_key, volume_key, scan_id, errors, success)


class HttpSink(Sink):
    """Überträgt an die Ingest-API eines entfernten DiskAtlas-Servers (z. B. Docker)."""

    def __init__(self, base_url: str, token: str = "", timeout: float = 120.0, client=None):
        self.capabilities = {}
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = client or httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1", headers=headers, timeout=timeout
        )

    def _post(self, path: str, payload: dict) -> dict:
        response = self.client.post(path, json=payload)
        response.raise_for_status()
        return response.json() if response.content else {}

    def report_disk(self, host: str, disk: DiskInfo) -> bool:
        reply = self._post("/ingest/disk", {"host": host, "disk": disk.model_dump(mode="json")})
        return not (isinstance(reply, dict) and reply.get("transfer_pending"))

    def report_connected(
        self, host: str, disk_keys: list[str], ports_info: dict | None = None
    ) -> None:
        self._post(
            "/ingest/connected",
            {"host": host, "disk_keys": disk_keys, "ports_info": ports_info,
             **self.capabilities},
        )

    def fetch_transfers(self) -> list[dict]:
        response = self.client.get("/ingest/transfers")
        response.raise_for_status()
        return response.json()

    def transfer_progress(self, item_id: str, bytes_done: int) -> bool:
        reply = self._post(f"/ingest/transfers/{item_id}/progress", {"bytes_done": bytes_done})
        return not reply.get("abort")

    def transfer_done(self, item_id: str, sha256: str, size: int, name: str, message: str) -> None:
        self._post(f"/ingest/transfers/{item_id}/done",
                   {"sha256": sha256, "size": size, "result_name": name, "message": message})

    def transfer_fail(self, item_id: str, message: str, retry: bool) -> None:
        self._post(f"/ingest/transfers/{item_id}/fail", {"message": message, "retry": retry})

    @staticmethod
    def _check_relay(response) -> None:
        if response.status_code in (404, 410):
            raise RelayGone(f"Übertragung nicht mehr aktuell ({response.status_code})")
        response.raise_for_status()

    def relay_put(self, item_id: str, n: int, data: bytes, epk: str | None, final: bool) -> str:
        headers = {"Content-Type": "application/octet-stream", "X-Final": "1" if final else "0"}
        if epk:
            headers["X-Epk"] = epk
        response = self.client.put(f"/ingest/relay/{item_id}/chunks/{n}", content=data,
                                   headers=headers)
        if response.status_code == 409:
            return "wait"
        if response.status_code == 507:
            return "full"
        self._check_relay(response)
        return "ok"

    def relay_status(self, item_id: str) -> dict:
        response = self.client.get(f"/ingest/relay/{item_id}/status")
        self._check_relay(response)
        return response.json()

    def relay_get(self, item_id: str, n: int) -> tuple[bytes, str, bool] | None:
        response = self.client.get(f"/ingest/relay/{item_id}/chunks/{n}")
        if response.status_code == 204:
            return None
        self._check_relay(response)
        return (response.content, response.headers.get("X-Epk", ""),
                response.headers.get("X-Final") == "1")

    def relay_ack(self, item_id: str, n: int) -> None:
        response = self.client.post(f"/ingest/relay/{item_id}/chunks/{n}/ack", json={})
        self._check_relay(response)

    def fetch_commands(self, host: str) -> list[dict]:
        response = self.client.get("/ingest/commands", params={"host": host})
        response.raise_for_status()
        return response.json()

    def report_command(self, command_id: int, ok: bool, message: str) -> None:
        self._post(f"/ingest/commands/{command_id}/result", {"ok": ok, "message": message})

    def begin_index(self, disk_key: str, volume_key: str) -> str:
        data = self._post("/ingest/index/begin", {"disk_key": disk_key, "volume_key": volume_key})
        return data["scan_id"]

    def add_files(self, disk_key, volume_key, scan_id, records) -> None:
        self._post(
            "/ingest/index/files",
            {
                "disk_key": disk_key,
                "volume_key": volume_key,
                "scan_id": scan_id,
                "files": [list(r) for r in records],
            },
        )

    def finish_index(self, disk_key, volume_key, scan_id, errors, success) -> None:
        self._post(
            "/ingest/index/finish",
            {
                "disk_key": disk_key,
                "volume_key": volume_key,
                "scan_id": scan_id,
                "errors": errors,
                "success": success,
            },
        )

    def close(self) -> None:
        self.client.close()
