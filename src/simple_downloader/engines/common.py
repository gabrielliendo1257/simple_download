from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from simple_downloader.domain.models import DownloadContext, DownloadOutput
from simple_downloader.domain.protocols import HttpClient

_RESUME_META_SUFFIX = ".resume.json"


@dataclass(frozen=True)
class ResumePlan:
    """Resultado de verificar el parcial en disco antes de reanudar.

    - offset: bytes válidos desde los que continuar (0 = descarga nueva).
    - valid: False si el parcial no corresponde y hay que reiniciar de cero.
    - reason: por qué se descartó el parcial (para avisar al usuario).
    """

    offset: int
    valid: bool = True
    reason: str | None = None


def _resume_meta_path(out_file: Path) -> Path:
    return out_file.with_name(out_file.name + _RESUME_META_SUFFIX)


def save_resume_meta(out_file: Path, *, url: str, total_bytes: int | None) -> None:
    """Escribe el sidecar con los metadatos del parcial (URL y tamaño
    esperado). Permite verificar que el parcial corresponde a esta
    descarga al reanudar."""
    meta = {"url": url, "total_bytes": total_bytes}
    try:
        _resume_meta_path(out_file).write_text(json.dumps(meta), encoding="utf-8")
    except OSError:
        pass


def clear_resume_meta(out_file: Path) -> None:
    try:
        _resume_meta_path(out_file).unlink(missing_ok=True)
    except OSError:
        pass


def discard_partial(out_file: Path) -> None:
    """Borra el parcial y su sidecar: el usuario canceló la descarga y
    no hay que reanudarla ni dejar basura en disco."""
    clear_resume_meta(out_file)
    try:
        out_file.unlink(missing_ok=True)
    except OSError:
        pass


def _load_resume_meta(out_file: Path) -> dict | None:
    try:
        raw = _resume_meta_path(out_file).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        meta = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(meta, dict):
        return None
    return meta


def resume_plan(
    out_file: Path,
    *,
    url: str | None = None,
    expected_total: int | None = None,
) -> ResumePlan:
    """Verifica el parcial en disco y decide desde dónde continuar.

    Reglas:
    - sin archivo (o vacío) -> offset 0, descarga nueva.
    - el sidecar apunta a otra URL -> parcial ajeno, reiniciar.
    - tamaño == esperado -> ya completo.
    - tamaño > esperado -> parcial corrupto, reiniciar.
    - 0 < tamaño < esperado (o esperado desconocido) -> reanudar.
    """
    if not out_file.exists() or out_file.stat().st_size == 0:
        return ResumePlan(offset=0)

    written = out_file.stat().st_size

    meta = _load_resume_meta(out_file)
    if url is not None and meta is not None and meta.get("url") != url:
        return ResumePlan(
            offset=0,
            valid=False,
            reason="el parcial en disco pertenece a otra descarga",
        )

    if expected_total is not None:
        if written == expected_total:
            return ResumePlan(offset=written)
        if written > expected_total:
            return ResumePlan(
                offset=0,
                valid=False,
                reason=(
                    f"el parcial ({written} bytes) supera el tamaño "
                    f"esperado ({expected_total})"
                ),
            )

    return ResumePlan(offset=written)


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
