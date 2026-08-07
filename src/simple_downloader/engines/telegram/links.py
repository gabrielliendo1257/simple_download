from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

_TELEGRAM_HOSTS = frozenset({"t.me", "telegram.me", "telegram.dog"})


@dataclass(frozen=True)
class TelegramLink:
    """Referencia a un mensaje de Telegram.

    - peer: username de canal/grupo o id numérico de canal privado
      (reconstruido con el prefijo -100 de los chats de tipo channel).
    - message_id: id del mensaje dentro del chat.
    """

    peer: str | int
    message_id: int


def parse_link(url: str) -> TelegramLink | None:
    """Interpreta links de Telegram (t.me / telegram.me / tg://).

    Solo los enlaces que identifican un mensaje sirven para descargar
    media; los de invitación (+HASH, joinchat) se rechazan.

    Formatos:
    - https://t.me/<username>/<msg_id>          canal/grupo público
    - https://t.me/c/<chat_id>/<msg_id>         canal/grupo privado
    - https://t.me/c/<chat_id>/<topic_id>/<msg_id>
        topic o comentario: el segmento intermedio es solo navegación
        (los ids de mensaje son globales al chat), se ignora.
    - variantes con query `?comment=`/`?t=`      se normalizan al mensaje base
    - tg://resolve?domain=<username>&post=<msg_id>
    """
    parts = urlsplit(url)

    if parts.scheme == "tg":
        if parts.netloc != "resolve":
            return None
        query = parse_qs(parts.query)
        domain = query.get("domain", [None])[0]
        post = query.get("post", [None])[0]
        if not domain or not post or not post.isdigit():
            return None
        return TelegramLink(peer=domain, message_id=int(post))

    if parts.netloc not in _TELEGRAM_HOSTS:
        return None

    # La query (?comment=, ?t=) apunta al mismo mensaje base: se ignora.
    segments = [segment for segment in parts.path.split("/") if segment]
    if segments and segments[0] == "c":
        # canal/grupo privado: /c/<chat_id>/<msg_id> o
        # /c/<chat_id>/<topic_id>/<msg_id> (topic ignorado)
        if len(segments) not in (3, 4) or not all(
            segment.isdigit() for segment in segments[1:]
        ):
            return None
        chat_id = int(segments[1])
        peer = -(10**12 + chat_id)
        return TelegramLink(peer=peer, message_id=int(segments[-1]))

    if len(segments) != 2 or not segments[1].isdigit():
        return None
    return TelegramLink(peer=segments[0], message_id=int(segments[1]))
