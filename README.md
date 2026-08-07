# Simple Downloader

Gestor de descargas con interfaz TUI (Terminal UI) construida con [Textual](https://textual.textualize.io/). Usa una arquitectura de *engines* por protocolo: cada tipo de URL se resuelve con la estrategia más adecuada, con fallback a [yt-dlp](https://github.com/yt-dlp/yt-dlp) como último recurso.

## Características

- **TUI completa**: cola de descargas con estados, progreso, velocidad, ETA, detalles, pausa/reanudar/cancelar/descartar.
- **Engines por protocolo** (Strategy pattern):
  - `hls` — playlists `.m3u8` (TS, fMP4, segmentos ofuscados en PNG, cifrado AES-128, referer automático).
  - `http` — descarga directa con GET streaming (`.mp4`, `.mp3`, `.zip`, …), sin depender del extractor *generic* de yt-dlp.
  - `telegram` — media de mensajes de Telegram con Telethon (un solo segmento: Telethon usa su propio pipe optimizado).
  - `yt-dlp` — catch-all con reintentos, paralelismo y resume ya resueltos por yt-dlp.
- **Probe de formato**: se descarga el primer segmento y se *olfatea* (TS/fMP4/PNG-wrapped); `HlsTask` lo baja localmente con reintentos por segmento. Si el formato no se reconoce, falla con un error claro.
- **Soporte fMP4**: `#EXT-X-MAP` (segmento de init) + fragmentos `.m4s`.
- **Referer automático**: los sitios que exigen `Referer` del propio dominio funcionan sin configuración; se puede sobreescribir por request.
- **Salida configurable**: nombre fijo, plantillas con placeholders (`{title}`, `{id}`, `{resolution}`, `{ext}`, `{date}`) y sobrescritura.
- **Machine states**: transiciones de estado validadas (`can_transition`), eventos sobre un `EventBus` desacoplado de la UI.
- **109 tests unitarios** con pytest.

## Requisitos

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/) (gestor del proyecto)
- Binario `yt-dlp` en el `PATH` (se detecta al arrancar)

## Instalación y uso

```bash
uv sync        # instala dependencias
uv run simple-downloader
```

> Si el binario `yt-dlp` no está instalado: `uv tool install yt-dlp` o tu gestor de paquetes.

### Atajos de la TUI

| Tecla | Acción |
|-------|--------|
| `a` / `Enter` en el campo de URL | Añadir descarga |
| `p` | Pausar |
| `r` | Reanudar |
| `x` | Cancelar (con confirmación) |
| `d` | Descartar de la lista (completada/fallida/cancelada) |
| `Enter` en la lista | Detalles de la descarga |
| `j` / `k` | Navegar en la lista |
| `q` | Salir |

Al pegar una URL, se abre un modal para ajustar nombre, directorio y opciones de salida; el título real se rellena en segundo plano vía `yt-dlp --dump-single-json` si no lo editas antes.

## Arquitectura

```
src/simple_downloader/
├── domain/           # modelos puros: DownloadRequest/Output/Context, estados, eventos
│   ├── protocols.py  # Engine, DownloadTask, HttpClient (Protocols, sin implementación)
│   ├── models.py     # dataclasses del dominio
│   └── state.py      # máquina de estados con transiciones validadas
├── engines/
│   ├── common.py     # origin()/http_with_context()/resolve_output()
│   ├── hls/          # engine HLS propio
│   │   ├── parser.py # parse_master / parse_media_playlist (TS + fMP4/EXT-X-MAP)
│   │   ├── fetch.py  # descarga de segmentos, AES-128, unwrap de PNG
│   │   ├── probe.py  # sniff_segment/probe_segment: clasifica el primer segmento
│   │   └── task.py   # HlsTask: descarga en paralelo, reintentos, escritura en orden
│   ├── http/         # HttpEngine + HttpDownloadTask (GET streaming)
│   └── ytdlp/        # adapter a yt-dlp como subprocess + progreso parseado
├── app/              # DownloadManager (jobs), DownloadScheduler (workers asyncio)
├── infra/            # AioHttpClient (get/get_range/stream), UserConfig
├── ui/               # DownloadApp (Textual), widgets, styles.tcss
├── sources.py        # YtDlpSource: metadata/formats/download vía subprocess
├── executor.py       # detección y registro de ejecutables externos (yt-dlp)
└── process.py        # ejecución de subprocesos asíncronos
```

### Capas

1. **domain** — modelos y protocolos sin dependencias externas; todo lo demás implementa los `Protocol`.
2. **engines** — un `Engine` por protocolo de descarga. El `EngineRegistry` selecciona el primero cuyo `supports(url)` devuelva `True`.
3. **app** — `DownloadManager` encola `DownloadJob`s; el `DownloadScheduler` los ejecuta con hasta 3 workers asíncronos y publica progreso/estado en el `EventBus`.
4. **ui** — suscribe el `EventBus` y refleja los cambios en la TUI. La UI no conoce detalles de los engines.

### Selección de engine

```
EngineRegistry (orden de registro):
  1. HlsEngine   → supports: el path de la URL termina en .m3u8
  2. HttpEngine  → supports: extensión directa en el path (.mp4, .mp3, …)
                  o en un query param (remote_control.php?file=video.mp4)
  3. YtDlpEngine → catch-all: soporta cualquier URL
```

La comprobación de `.m3u8` se hace sobre el **path** (`urlsplit().path`), no sobre la URL completa: las playlists reales llevan query string (`master.m3u8?hash=…&expires=…&ip=…`) que rompería un simple `endswith`.

### HLS: probe de formato

El `HlsEngine` no decide por la extensión, sino por el contenido del stream:

```
playlist .m3u8
  └─ descarga el primer segmento (Range: bytes=0-8191 si el cliente lo soporta)
       ├─ PNG magic  → PNG_WRAPPED → HlsTask (desempaque + AES), salida .mp4
       ├─ box mp4    → FMP4        → HlsTask (init + fragmentos .m4s), salida .mp4
       ├─ sync 0x47  → TS          → HlsTask, salida .ts
       └─ otro       → UNKNOWN     → error claro (no se reconoce el formato)
```

Todos los formatos se descargan con `HlsTask` (paralelo, escritura en orden, AES-128, referer, reintentos por segmento con backoff). Esto evita depender del binario `yt-dlp` para HLS; `yt-dlp` queda como catch-all para lo demás.

### Salida (`DownloadOutput`)

Reglas de resolución (`engines/common.py:resolve_output`):

1. `filename` presente → `directory / filename`
2. `template` presente → placeholders reemplazados
3. ninguno → el engine decide (basename de la URL, uuid, etc.)

Templates disponibles: `{title}`, `{id}`, `{resolution}`, `{ext}`, `{date}`. El path de destino se crea automáticamente (`create_directories`). En `YtDlpEngine` los placeholders se traducen a la sintaxis de yt-dlp (`%(title)s`, …).

### Contexto por request (`DownloadContext`)

- `referer` — si no se indica, se deriva del origin de la URL (`https://host/`).
- `user_agent`, `headers` — cabeceras adicionales.
- `timeout_sec`, `max_parallel_segments` — límites de red y de paralelismo en HLS.

## Telegram (Telethon)

El engine de Telegram descarga el **media de un mensaje** usando una cuenta
de usuario (no un bot) vía [Telethon](https://docs.telethon.dev/) (MTProto,
asyncio nativo). El archivo es **un solo segmento**: Telethon ya optimiza la
transferencia con su propio pipe; el paralelismo solo añadiría latencia.
Escribe directo en el archivo de salida (como los demás engines): al pausar,
el parcial queda en disco y al reanudar continúa desde el tamaño actual
(`iter_download(offset=...)`), nunca desde cero.

Links soportados (solo los que identifican un mensaje; las invitaciones
`+HASH`/`joinchat` se rechazan):

- `https://t.me/<canal>/<msg_id>` — canal/grupo público
- `https://t.me/c/<chat_id>/<msg_id>` — canal/grupo privado
- `https://t.me/c/<chat_id>/<topic_id>/<msg_id>` — topic/comentario
  (el id intermedio es solo navegación y se ignora)
- variantes con query `?comment=`/`?t=` — se normalizan al mensaje base
- `tg://resolve?domain=<canal>&post=<msg_id>`

### Setup (una sola vez)

1. Configura la sección `telegram` de `~/.config/simple-downloader/config.json`
   (`api_id`/`api_hash` se obtienen en https://my.telegram.org):
   ```json
   {
     "telegram": {
       "enabled": true,
       "api_id": 123456,
       "api_hash": "tu_api_hash",
       "session_name": "simple_downloader"
     }
   }
   ```
2. Inicia sesión (pide teléfono + código, guarda la sesión en
   `~/.config/simple-downloader/`):
   ```bash
   uv run simple-downloader --telegram-login
   ```

Después solo pegas el link en la TUI como cualquier otra URL. El nombre que
se muestra en la UI es el que reporta Telegram (metadatos del documento, que
Telethon ya obtiene al resolver el mensaje); si el documento no trae nombre,
se usa `telegram-<msg_id>.<ext>`.

### Arquitectura del módulo

```
src/simple_downloader/engines/telegram/
├── links.py    # parse_link: t.me / telegram.me / tg://
├── client.py   # TelegramClientProvider: sesión persistente, una sola conexión
├── task.py     # TelegramDownloadTask: 1 segmento, progreso, pausa/resume desde el parcial
└── engine.py   # TelegramEngine: resuelve el mensaje (metadatos) y descarga
```

El engine se registra tras `HttpEngine` y antes del catch-all de yt-dlp.
Los tests usan un `FakeClient` (sin red ni credenciales).

## Tests

```bash
uv run pytest            # 109 tests
uv run black src tests   # formateo
```

Cobertura de tests por módulo: parser (master/media/fMP4/init), crypt (AES-128 + PKCS7), fetch, `HlsTask` (paralelo, orden, fallos), probe (sniff/TS/fMP4/PNG/Range/desconocido), `HttpEngine` (directo + wrapper en query), `TelegramEngine` (links t.me/tg://, resolución de mensaje, task 1-segmento), registry, output/context, estado del job, config.

## Estado del proyecto

- [x] TUI conectada al backend (estados, progreso, ETA, pausa/resume/cancel)
- [x] `HttpEngine` para descargas directas (path + query param)
- [x] HLS propio: TS, AES-128, fMP4, PNG-wrap, referer, retries
- [x] Probe de formato (TS/fMP4/PNG-wrapped/desconocido)
- [x] Output con templates y referer configurable
- [x] `TelegramEngine` (Telethon): links t.me/tg://, un solo segmento
- [ ] Preview de metadata en la TUI (el `VideoMetadata` ya existe en `YtDlpSource.metadata()`)
- [ ] Resume de descargas HTTP/HLS propias
- [ ] Persistencia de jobs (`DownloadJobRepository` definido, sin implementación)
