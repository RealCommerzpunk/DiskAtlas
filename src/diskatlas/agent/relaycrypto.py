"""Ende-zu-Ende-Verschlüsselung für Übertragungen über den Relay.

Jeder Agent hat ein dauerhaftes X25519-Schlüsselpaar; der öffentliche Teil geht beim Heartbeat an
den Server. Der Sender erzeugt je Datei einen einmaligen Schlüssel, leitet mit dem öffentlichen
Schlüssel des Empfängers per ECDH + HKDF einen Dateischlüssel ab und verschlüsselt jedes Stück mit
AES-256-GCM (Nonce = Stücknummer, zusätzlich beglaubigt: Auftrags-ID, Stücknummer, Endekennzeichen –
Umordnen, Auslassen und Vertauschen fallen auf). Das letzte Stück enthält SHA-256 und Größe des
Klartexts. Der Server sieht nur Chiffretext unter Zufallsnamen.

Grenze: Ein aktiv manipulierender Server könnte dem Sender einen falschen öffentlichen Schlüssel
unterschieben; gegen bloßes Mitlesen und Ablegen der Daten schützt das Verfahren.
"""

from __future__ import annotations

import base64
import contextlib
import os
import struct
from pathlib import Path

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
        X25519PublicKey,
    )
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    AVAILABLE = True
except ImportError:  # pragma: no cover - ohne das Paket gibt es keine Relay-Übertragungen
    AVAILABLE = False

INFO = b"diskatlas-relay-v1"
TRAILER = struct.Struct(">32sQ")  # SHA-256 + Größe


class CryptoError(Exception):
    """Entschlüsselung/Prüfung fehlgeschlagen (Daten verfälscht oder falscher Schlüssel)."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _raw_public(key) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def load_or_create_key(path: Path) -> tuple[object, str]:
    """Das Schlüsselpaar dieses Agenten (beim ersten Mal erzeugt, nur für den Benutzer lesbar)."""
    try:
        stored = base64.b64decode(path.read_text().strip())
        private = X25519PrivateKey.from_private_bytes(stored)
    except (OSError, ValueError):
        private = X25519PrivateKey.generate()
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption())
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(_b64(raw))
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
    return private, _b64(_raw_public(private.public_key()))


def _file_key(shared: bytes, item_id: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=INFO + item_id.encode("ascii", "ignore")).derive(shared)


def _nonce(index: int) -> bytes:
    return struct.pack(">4xQ", index)  # 12 Bytes; der Schlüssel gilt nur für diese eine Datei


def _aad(item_id: str, index: int, final: bool) -> bytes:
    return item_id.encode("ascii", "ignore") + struct.pack(">QB", index, 1 if final else 0)


class Sealer:
    """Sender: verschlüsselt die Stücke einer Datei für den Empfänger."""

    def __init__(self, target_pubkey_b64: str, item_id: str):
        ephemeral = X25519PrivateKey.generate()
        peer = X25519PublicKey.from_public_bytes(base64.b64decode(target_pubkey_b64))
        self.item_id = item_id
        self._aes = AESGCM(_file_key(ephemeral.exchange(peer), item_id))
        self.epk = _b64(_raw_public(ephemeral.public_key()))

    def seal(self, index: int, data: bytes, final: bool = False) -> bytes:
        return self._aes.encrypt(_nonce(index), data, _aad(self.item_id, index, final))

    def seal_trailer(self, index: int, sha256: bytes, size: int) -> bytes:
        return self.seal(index, TRAILER.pack(sha256, size), final=True)


class Opener:
    """Empfänger: entschlüsselt die Stücke mit dem eigenen privaten Schlüssel."""

    def __init__(self, private_key, epk_b64: str, item_id: str):
        try:
            peer = X25519PublicKey.from_public_bytes(base64.b64decode(epk_b64))
            self._aes = AESGCM(_file_key(private_key.exchange(peer), item_id))
        except ValueError as exc:
            raise CryptoError("Ungültiger Schlüssel des Senders") from exc
        self.item_id = item_id

    def open(self, index: int, blob: bytes, final: bool = False) -> bytes:
        try:
            return self._aes.decrypt(_nonce(index), blob, _aad(self.item_id, index, final))
        except Exception as exc:  # InvalidTag u. a.
            raise CryptoError("Stück lässt sich nicht entschlüsseln (verfälscht?)") from exc

    def open_trailer(self, index: int, blob: bytes) -> tuple[bytes, int]:
        plain = self.open(index, blob, final=True)
        if len(plain) != TRAILER.size:
            raise CryptoError("Ungültiger Abschluss")
        digest, size = TRAILER.unpack(plain)
        return digest, size
