"""Minimal single-page PDF renderer (pure Python, no system deps).

Produces a valid PDF containing a title and a list of text lines. This is
enough for the MVP's downloadable report; a production build can swap in
WeasyPrint for richer layout without changing the endpoint's contract.
"""

from __future__ import annotations


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def render_pdf(title: str, lines: list[str]) -> bytes:
    """Return PDF bytes for a titled, line-based document."""
    # Content stream: draw the title larger, then each line below it.
    content_parts = ["BT /F1 18 Tf 50 780 Td (" + _escape(title) + ") Tj ET"]
    y = 750
    for line in lines:
        content_parts.append(
            "BT /F1 11 Tf 50 " + str(y) + " Td (" + _escape(line) + ") Tj ET"
        )
        y -= 16
        if y < 60:
            break
    content = "\n".join(content_parts).encode("latin-1", "replace")

    # Assemble a minimal object graph: catalog, pages, page, font, content.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(content)).encode()
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    ).encode()
    return bytes(out)
