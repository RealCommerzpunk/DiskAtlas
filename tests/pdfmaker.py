"""Kleine Text-PDFs für Tests (ohne zusätzliche Bibliothek)."""

from __future__ import annotations


def text_pdf(lines: list[str], created: str | None = "D:20261020143000Z") -> bytes:
    """Ein einseitiges PDF mit Helvetica-Textzeilen und optionalem Erstellungsdatum."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    body = "BT /F1 11 Tf 50 800 Td 14 TL " + " ".join(f"({esc(t)}) Tj T*" for t in lines) + " ET"
    stream = body.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    info = f"<< /Producer (nwipe-test) /CreationDate ({created}) >>".encode() if created else None
    if info:
        objects.append(info)
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    trailer = b"<< /Size %d /Root 1 0 R" % (len(objects) + 1)
    if info:
        trailer += b" /Info %d 0 R" % len(objects)
    out += b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)
