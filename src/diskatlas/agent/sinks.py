"""Ziele für Scan-Ergebnisse: direkt in die Datenbank oder per HTTP an einen DiskAtlas-Server."""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from diskatlas.db import Database
from diskatlas.probe.types import DiskInfo, FileRecord
from diskatlas.services import ingest


class Sink(ABC):
    @abstractmethod
    def report_disk(self, host: str, disk: DiskInfo) -> None: ...

    @abstractmethod
    def report_connected(self, host: str, disk_keys: list[str]) -> None: ...

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

    def close(self) -> None:  # noqa: B027 - optionaler Hook
        pass


class DatabaseSink(Sink):
    def __init__(self, db: Database):
        self.db = db

    def report_disk(self, host: str, disk: DiskInfo) -> None:
        with self.db.session() as s:
            ingest.upsert_disk(s, host, disk)

    def report_connected(self, host: str, disk_keys: list[str]) -> None:
        with self.db.session() as s:
            ingest.mark_connected(s, host, disk_keys)

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
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = client or httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1", headers=headers, timeout=timeout
        )

    def _post(self, path: str, payload: dict) -> dict:
        response = self.client.post(path, json=payload)
        response.raise_for_status()
        return response.json() if response.content else {}

    def report_disk(self, host: str, disk: DiskInfo) -> None:
        self._post("/ingest/disk", {"host": host, "disk": disk.model_dump(mode="json")})

    def report_connected(self, host: str, disk_keys: list[str]) -> None:
        self._post("/ingest/connected", {"host": host, "disk_keys": disk_keys})

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
