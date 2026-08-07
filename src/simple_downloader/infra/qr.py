from __future__ import annotations

import io


def qr_ascii(url: str) -> str:
    """Renderiza una URL como QR ASCII (bloques unicode, monocromo)."""
    try:
        from qrcode import QRCode
    except ImportError:
        return url
    qr = QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    buffer = io.StringIO()
    qr.print_ascii(out=buffer, invert=True)
    return buffer.getvalue()
