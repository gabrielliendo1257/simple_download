from __future__ import annotations

from simple_downloader.domain.protocols import Engine


class NoEngineError(LookupError):
    def __init__(self, url: str) -> None:
        super().__init__(f"no engine supports url: {url!r}")


class EngineRegistry:
    def __init__(self, engines: list[Engine] | None = None) -> None:
        self._engines: list[Engine] = engines or []

    def register(self, engine: Engine) -> Engine:
        self._engines.append(engine)
        return engine

    def engine_for(self, url: str) -> Engine:
        for engine in self._engines:
            if engine.supports(url):
                return engine
        raise NoEngineError(url)

    @property
    def engines(self) -> tuple[Engine, ...]:
        return tuple(self._engines)
