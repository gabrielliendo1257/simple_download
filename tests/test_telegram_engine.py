from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from simple_downloader.domain.models import DownloadOutput, DownloadRequest
from simple_downloader.engines.telegram import (
    TelegramEngine,
    parse_link,
)
from simple_downloader.engines.telegram.client import (
    TelegramClientProvider,
    TelegramThrottledError,
)
from simple_downloader.engines.telegram.links import TelegramLink
from simple_downloader.engines.telegram.task import TelegramDownloadTask
from simple_downloader.infra.config import TelegramConfig


class FakeMessage:
    def __init__(self, media: object = object(), name: str = "video.mp4") -> None:
        self.media = media
        self.file = type("File", (), {"name": name, "ext": ".mp4", "size": 1024})()


class FakeClient:
    DATA = bytes(range(256)) * 4  # 1024 bytes

    def __init__(self, message: FakeMessage | None = None) -> None:
        self.message = message
        self.worker_loop = None

    async def get_messages(self, peer, ids=None):
        self.worker_loop = asyncio.get_running_loop()
        self.peer = peer
        return self.message

    async def iter_download(self, file, *, offset=0, **kwargs):
        for start in range(offset, len(self.DATA), 128):
            yield self.DATA[start : start + 128]


class FakeProvider:
    """Provider de tests: misma interfaz que TelegramClientProvider pero
    todo corre en el loop del test (sin hilos)."""

    def __init__(self, client: FakeClient) -> None:
        self._client = client

    async def get_message(self, peer, message_id):
        return await self._client.get_messages(peer, ids=message_id)

    async def download_to(
        self, message, out_file, offset=0, progress_callback=None, waiting_callback=None
    ):
        with open(out_file, "wb" if offset == 0 else "ab") as handle:
            async for chunk in self._client.iter_download(message.media, offset=offset):
                handle.write(chunk)
                if progress_callback is not None:
                    progress_callback(handle.tell(), len(self._client.DATA))
        return out_file

    async def get_me(self):
        return object()

    async def disconnect(self) -> None:
        pass


class ThreadedFakeProvider(TelegramClientProvider):
    """Provider real con hilo worker + loop propio, pero con un cliente
    falso: verifica que el bridge no toca el loop del llamante."""

    def __init__(self, client: FakeClient) -> None:
        super().__init__(config=None)
        self._fake = client

    async def _connect(self):
        return self._fake


def _engine(client: FakeClient) -> TelegramEngine:
    return TelegramEngine(client_provider=FakeProvider(client))


# ── parse_link ─────────────────────────────────────────────────────────────


def test_parse_public_channel_link() -> None:
    link = parse_link("https://t.me/mi_canal/123")
    assert link == TelegramLink(peer="mi_canal", message_id=123)


def test_parse_private_channel_link_reconstructs_peer() -> None:
    link = parse_link("https://t.me/c/1085187789/17")
    assert link is not None
    assert link.peer == -(10**12 + 1085187789)
    assert link.message_id == 17


def test_parse_private_channel_topic_link_ignores_topic() -> None:
    link = parse_link("https://t.me/c/1085187789/15/789")
    assert link is not None
    assert link.peer == -(10**12 + 1085187789)
    assert link.message_id == 789


def test_parse_query_variants_normalize_to_base_message() -> None:
    assert parse_link("https://t.me/mi_canal/123?t=100") == TelegramLink(
        peer="mi_canal", message_id=123
    )
    assert parse_link("https://t.me/mi_canal/123?comment=4") == TelegramLink(
        peer="mi_canal", message_id=123
    )
    assert parse_link("https://t.me/c/1085187789/17?t=200") == TelegramLink(
        peer=-(10**12 + 1085187789), message_id=17
    )


def test_parse_tg_link() -> None:
    link = parse_link("tg://resolve?domain=mi_canal&post=42")
    assert link == TelegramLink(peer="mi_canal", message_id=42)


def test_parse_rejects_invalid_links() -> None:
    assert parse_link("https://example.com/not-telegram") is None
    assert parse_link("https://t.me/mi_canal") is None  # sin message id
    assert parse_link("https://t.me/mi_canal/abc") is None
    assert parse_link("tg://resolve?domain=x") is None


def test_parse_rejects_invite_links() -> None:
    assert parse_link("https://t.me/+ABCDEFG") is None  # invitación
    assert parse_link("https://t.me/joinchat/ABCDEFG") is None
    assert parse_link("https://t.me/c/123/456/789/1011") is None  # demasiados segmentos


# ── engine ─────────────────────────────────────────────────────────────────


def test_telegram_engine_supports_tme_links() -> None:
    engine = _engine(FakeClient())
    assert engine.supports("https://t.me/mi_canal/123")
    assert engine.supports("tg://resolve?domain=x&post=1")
    assert not engine.supports("https://cdn.x.com/file.mp4")


def test_create_task_resolves_message_and_uses_document_name(tmp_path) -> None:
    engine = _engine(FakeClient(FakeMessage(name="mi_video.mp4")))
    request = DownloadRequest(
        url="https://t.me/mi_canal/123",
        output=DownloadOutput(directory=tmp_path),
    )

    task = asyncio.run(engine.create_task(request))

    assert isinstance(task, TelegramDownloadTask)
    assert task.out_file == tmp_path / "mi_video.mp4"
    assert task.title == "mi_video.mp4"


def test_create_task_fallback_name_without_document(tmp_path) -> None:
    engine = _engine(FakeClient(FakeMessage(name=None)))
    request = DownloadRequest(
        url="https://t.me/mi_canal/123",
        output=DownloadOutput(directory=tmp_path),
    )

    task = asyncio.run(engine.create_task(request))

    assert task.out_file.name == "telegram-123.mp4"
    assert task.title == "telegram-123.mp4"


def test_create_task_raises_when_message_has_no_media() -> None:
    engine = _engine(FakeClient(FakeMessage(media=None)))
    request = DownloadRequest(url="https://t.me/mi_canal/123")

    with pytest.raises(ValueError, match="no tiene media"):
        asyncio.run(engine.create_task(request))


def test_create_task_raises_when_not_configured() -> None:
    engine = TelegramEngine(client_provider=TelegramClientProvider(TelegramConfig()))
    request = DownloadRequest(url="https://t.me/mi_canal/123")

    with pytest.raises(ValueError, match="no está configurado"):
        asyncio.run(engine.create_task(request))


def test_validate_passes_when_message_has_media() -> None:
    engine = _engine(FakeClient(FakeMessage()))

    assert asyncio.run(engine.validate("https://t.me/mi_canal/123")) is None


def test_validate_raises_when_message_has_no_media() -> None:
    engine = _engine(FakeClient(FakeMessage(media=None)))

    with pytest.raises(ValueError, match="no tiene media"):
        asyncio.run(engine.validate("https://t.me/mi_canal/123"))


# ── task: un solo segmento ─────────────────────────────────────────────────


async def test_task_downloads_single_file(tmp_path) -> None:
    client = FakeClient()
    task = TelegramDownloadTask(
        message=FakeMessage(),
        out_file=tmp_path / "out.mp4",
        provider=FakeProvider(client),
    )

    progress: list = []
    async for item in task.progress():
        progress.append(item)
    result = await task.finalize()

    assert (tmp_path / "out.mp4").read_bytes() == FakeClient.DATA
    assert result.exit_code == 0
    assert result.output == tmp_path / "out.mp4"
    assert progress[-1].downloaded_bytes == len(FakeClient.DATA)
    assert progress[-1].total_bytes == len(FakeClient.DATA)


async def test_task_cancel_discards_partial(tmp_path) -> None:
    class SlowClient(FakeClient):
        async def iter_download(self, file, *, offset=0, **kwargs):
            for start in range(offset, len(self.DATA), 128):
                yield self.DATA[start : start + 128]
                await asyncio.sleep(0.05)

    out = tmp_path / "out.mp4"
    task = TelegramDownloadTask(
        message=FakeMessage(),
        out_file=out,
        provider=FakeProvider(SlowClient()),
    )

    async def consume() -> None:
        async for _ in task.progress():
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.02)
    await task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert not out.exists()  # cancelar abandona: sin basura en disco


async def test_task_pause_keeps_partial_on_disk(tmp_path) -> None:
    class SlowClient(FakeClient):
        async def iter_download(self, file, *, offset=0, **kwargs):
            for start in range(offset, len(self.DATA), 128):
                yield self.DATA[start : start + 128]
                await asyncio.sleep(0.05)

    out = tmp_path / "out.mp4"
    task = TelegramDownloadTask(
        message=FakeMessage(),
        out_file=out,
        provider=FakeProvider(SlowClient()),
    )

    async def consume() -> None:
        async for _ in task.progress():
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.02)
    await task.pause()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert out.exists()
    assert out.stat().st_size == 128  # pausar conserva el parcial


async def test_task_resume_continues_from_partial(tmp_path) -> None:
    out = tmp_path / "out.mp4"
    out.write_bytes(FakeClient.DATA[:512])  # parcial previo (pausado a la mitad)

    task = TelegramDownloadTask(
        message=FakeMessage(),
        out_file=out,
        provider=FakeProvider(FakeClient()),
    )

    last = None
    async for item in task.progress():
        last = item
    await task.finalize()

    assert out.read_bytes() == FakeClient.DATA
    assert last is not None
    assert last.downloaded_bytes == len(FakeClient.DATA)
    assert last.total_bytes == len(FakeClient.DATA)


async def test_task_skips_download_when_file_complete(tmp_path) -> None:
    out = tmp_path / "out.mp4"
    out.write_bytes(FakeClient.DATA)  # ya descargado antes

    task = TelegramDownloadTask(
        message=FakeMessage(),
        out_file=out,
        provider=FakeProvider(FakeClient()),
    )

    async for _ in task.progress():
        pass
    await task.finalize()

    assert out.read_bytes() == FakeClient.DATA


async def test_task_fallback_restarts_when_resume_fails(tmp_path) -> None:
    class FlakyProvider(FakeProvider):
        """Rechaza reanudar desde un offset (como un servidor que cambió
        el archivo) pero permite descargar desde cero."""

        def __init__(self, client: FakeClient) -> None:
            super().__init__(client)
            self.failed_offset: int | None = None

        async def download_to(
            self,
            message,
            out_file,
            offset=0,
            progress_callback=None,
            waiting_callback=None,
        ):
            if offset > 0:
                self.failed_offset = offset
                raise RuntimeError("offset no soportado")
            return await super().download_to(
                message, out_file, 0, progress_callback, waiting_callback
            )

    out = tmp_path / "out.mp4"
    out.write_bytes(FakeClient.DATA[:512])  # parcial previo

    task = TelegramDownloadTask(
        message=FakeMessage(),
        out_file=out,
        provider=FlakyProvider(FakeClient()),
    )

    last = None
    async for item in task.progress():
        last = item
    result = await task.finalize()

    assert out.read_bytes() == FakeClient.DATA  # se reinició y completó
    assert task.resume_fallback is True
    assert "byte 512" in (task.resume_fallback_reason or "")
    assert last is not None
    assert last.downloaded_bytes == len(FakeClient.DATA)


# ── provider: hilo worker con loop propio ──────────────────────────────────


async def test_provider_runs_telethon_on_worker_loop() -> None:
    client = FakeClient(FakeMessage())
    provider = ThreadedFakeProvider(client)

    message = await provider.get_message("mi_canal", 1)

    assert message is not None
    assert client.worker_loop is not None
    assert client.worker_loop is not asyncio.get_running_loop()
    assert client.peer == "mi_canal"
    await provider.disconnect()


async def test_provider_download_bridges_progress_back(tmp_path) -> None:
    client = FakeClient()
    provider = ThreadedFakeProvider(client)
    progress: list[tuple[int, int]] = []
    main_loop = asyncio.get_running_loop()

    def on_progress(received: int, total: int) -> None:
        progress.append((received, total))

    await provider.download_to(
        FakeMessage(),
        out_file=tmp_path / "out.mp4",
        progress_callback=on_progress,
    )

    assert (tmp_path / "out.mp4").read_bytes() == FakeClient.DATA
    assert progress[-1] == (len(FakeClient.DATA), len(FakeClient.DATA))
    await provider.disconnect()


# ── config ─────────────────────────────────────────────────────────────────


def test_telegram_config_requires_credentials() -> None:
    assert not TelegramConfig().is_usable()
    assert not TelegramConfig(enabled=True).is_usable()
    assert TelegramConfig(enabled=True, api_id=1, api_hash="x").is_usable()


# ── políticas de conexión: semáforo y flood waits ─────────────────────────


async def test_flood_wait_becomes_throttled_error(tmp_path) -> None:
    from telethon.errors.rpcerrorlist import FloodWaitError

    class FloodingClient(FakeClient):
        async def iter_download(self, file, *, offset=0, **kwargs):
            raise FloodWaitError(None, capture=32)
            yield  # pragma: no cover

    provider = TelegramClientProvider(config=None)
    provider._dl_slots = asyncio.Semaphore(1)

    with pytest.raises(TelegramThrottledError) as excinfo:
        await provider._download_guarded(
            FloodingClient(),
            FakeMessage(),
            tmp_path / "out.mp4",
            offset=0,
            progress_callback=None,
        )

    assert excinfo.value.wait_seconds == 32
    assert "esperá 32s" in str(excinfo.value)


async def test_concurrent_downloads_respect_semaphore(tmp_path) -> None:
    active = 0
    max_active = 0

    class SlowClient(FakeClient):
        async def iter_download(self, file, *, offset=0, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.02)
                yield b"x" * 16
            finally:
                active -= 1

    provider = TelegramClientProvider(config=None)
    provider._dl_slots = asyncio.Semaphore(2)
    client = SlowClient()

    await asyncio.gather(
        *[
            provider._download_guarded(
                client,
                FakeMessage(),
                tmp_path / f"out{i}.mp4",
                offset=0,
                progress_callback=None,
            )
            for i in range(4)
        ]
    )

    assert max_active <= 2


async def test_task_does_not_fallback_when_throttled(tmp_path) -> None:
    from simple_downloader.engines.telegram.client import TelegramThrottledError

    class ThrottledProvider(FakeProvider):
        async def download_to(
            self,
            message,
            out_file,
            offset=0,
            progress_callback=None,
            waiting_callback=None,
        ):
            raise TelegramThrottledError(32, caused_by="flood wait")

    out = tmp_path / "out.mp4"
    out.write_bytes(b"partial")  # parcial previo

    task = TelegramDownloadTask(
        message=FakeMessage(),
        out_file=out,
        provider=ThrottledProvider(FakeClient()),
    )

    with pytest.raises(TelegramThrottledError):
        async for _ in task.progress():
            pass
        await task.finalize()

    assert task.resume_fallback is False  # no reinicia desde cero
    assert out.read_bytes() == b"partial"  # ni toca el parcial


async def test_task_reports_waiting_for_slot(tmp_path) -> None:
    """Mientras espera el semáforo, el task expone waiting_for_slot y el
    loop de progreso emite eventos (0 bytes) para que la UI avise."""

    class WaitingProvider(FakeProvider):
        def __init__(self, client: FakeClient) -> None:
            super().__init__(client)
            self.released = asyncio.Event()

        async def download_to(
            self,
            message,
            out_file,
            offset=0,
            progress_callback=None,
            waiting_callback=None,
        ):
            waiting_callback(True)
            await self.released.wait()
            waiting_callback(False)
            return await super().download_to(
                message, out_file, offset, progress_callback, waiting_callback
            )

    provider = WaitingProvider(FakeClient())
    task = TelegramDownloadTask(
        message=FakeMessage(),
        out_file=tmp_path / "out.mp4",
        provider=provider,
    )

    async def consume() -> list:
        seen = []
        async for item in task.progress():
            seen.append((item.downloaded_bytes, task.waiting_for_slot))
            if len(seen) >= 2:
                break
        return seen

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    assert task.waiting_for_slot is True  # está esperando turno

    provider.released.set()
    seen = await consumer
    assert seen[0] == (0, True)  # emitió progreso avisando la espera

    async for _ in task.progress():
        pass
    await task.finalize()
    assert task.waiting_for_slot is False
