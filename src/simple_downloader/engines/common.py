from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from simple_downloader.domain.models import DownloadContext, DownloadOutput
from simple_downloader.domain.protocols import HttpClient


def origin(url: str) -> str:
    """Devuelve la URL base (scheme://host/) usada como Referer.

    El sitio real rechaza peticiones sin el header referer apuntando
    a su dominio.
    """
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/"


def http_with_context(
    http: HttpClient, url: str, context: DownloadContext | None = None
) -> HttpClient:
    """Aplica el contexto del usuario al cliente HTTP.

    Prioridad del referer: el que venga en context > derivado del origin.
    Si el cliente no soporta headers (fakes en tests), se devuelve tal cual.
    """
    if context is None:
        return http_with_referer(http, url)

    headers = dict(context.headers)
    if context.user_agent is not None:
        headers.setdefault("user-agent", context.user_agent)
    if context.referer is not None:
        headers["referer"] = context.referer
    else:
        headers["referer"] = origin(url)

    with_headers = getattr(http, "with_headers", None)
    if with_headers is not None:
        return with_headers(headers)
    with_referer = getattr(http, "with_referer", None)
    if with_referer is not None:
        return with_referer(headers["referer"])
    return http


def http_with_referer(http: HttpClient, url: str) -> HttpClient:
    """Envuelve el cliente para que mande el referer de la URL base.

    Si el cliente no soporta headers (fakes en tests), se devuelve tal cual.
    """
    with_referer = getattr(http, "with_referer", None)
    if with_referer is not None:
        return with_referer(origin(url))
    return http


def resolve_output(
    request_url: str,
    output: DownloadOutput | None,
    *,
    default_name: str,
    ext: str | None = None,
    media: dict[str, str] | None = None,
) -> Path:
    """Resuelve el path de salida siguiendo las reglas de DownloadOutput.

    - filename presente -> directory / filename (+ ext si le falta)
    - template presente -> placeholders reemplazados
    - ninguno -> default_name del engine
    """
    media = media or {}
    if output is None:
        return Path(default_name)

    media_with_ext = dict(media)
    if ext is not None:
        media_with_ext.setdefault("ext", ext.lstrip("."))

    name = output.filename or output.template or default_name
    resolved = _render_template(name, media_with_ext)

    if ext is not None and not Path(resolved).suffix:
        resolved = f"{resolved}.{ext.lstrip('.')}"

    path = output.directory / resolved
    if output.create_directories:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _render_template(name: str, media: dict[str, str]) -> str:
    today = date.today().isoformat()
    values = {
        "date": today,
        "ext": "",
        "id": "",
        "resolution": "",
        "title": "",
    }
    values.update({key: value or "" for key, value in media.items()})
    return name.format(**values)
