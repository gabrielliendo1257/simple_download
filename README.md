# Simple Downloader

Gestor de descargas con interfaz TUI (Terminal UI) construida con [Textual](https://textual.textualize.io/). Usa una arquitectura de *engines* por protocolo: cada tipo de URL se resuelve con la estrategia más adecuada, con fallback a [yt-dlp](https://github.com/yt-dlp/yt-dlp) como último recurso.

## Características

- **TUI completa**: cola de descargas con estados, progreso, velocidad, ETA, detalles, pausa/reanudar/cancelar/descartar.
- **Engines por protocolo** (Strategy pattern):
  - `hls` — playlists `.m3u8` (TS, fMP4, segmentos ofuscados en PNG, cifrado AES-128, referer automático, selector de variantes).
  - `http` — descarga directa con GET streaming (`.mp4`, `.mp3`, `.zip`, …) y resume desde el parcial, sin depender del extractor *generic* de yt-dlp.
  - `telegram` — media de mensajes de Telegram con Telethon (un solo segmento: Telethon usa su propio pipe optimizado).
  - `yt-dlp` — catch-all con reintentos, paralelismo y resume ya resueltos por yt-dlp.
- **Probe de formato**: se descarga el primer segmento y se *olfatea* (TS/fMP4/PNG-wrapped); `HlsTask` lo baja localmente con reintentos por segmento. Si el formato no se reconoce, falla con un error claro.
- **Soporte fMP4**: `#EXT-X-MAP` (segmento de init) + fragmentos `.m4s`.
- **Referer automático**: los sitios que exigen `Referer` del propio dominio funcionan sin configuración; se puede sobreescribir por request.
- **Salida configurable**: nombre fijo, plantillas con placeholders (`{title}`, `{id}`, `{resolution}`, `{ext}`, `{date}`) y sobrescritura.
- **Config en caliente**: se re-lee `config.json` al abrir el modal de descarga (directorio por defecto, cookies de yt-dlp) o con `Ctrl+R`, sin reiniciar la app.
- **Machine states**: transiciones de estado validadas (`can_transition`), eventos sobre un `EventBus` desacoplado de la UI.
- **Persistencia de jobs**: catálogo SQLite (`SqliteRepository`) que guarda el historial de descargas.
- **206 tests unitarios** con pytest.

## Aviso legal

Esta herramienta solo facilita la descarga de contenido sobre el cual tengas
derechos o permiso explícito del titular (o que esté bajo una licencia que lo
permita). No la uses para descargar material con derechos de autor sin
autorización: es tu responsabilidad verificar la legalidad de cada descarga.
El proyecto se distribuye sin garantías (ver `LICENSE`).

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

### Configuración

La app crea `~/.config/simple-downloader/config.json` con defaults al primer arranque:

```json
{
  "directory": "downloads",
  "telegram": {
    "enabled": true,
    "api_id": 0,
    "api_hash": "",
    "session_name": "simple_downloader"
  }
}
```

La sección `telegram` requiere `api_id`/`api_hash` (se obtienen en my.telegram.org). El directorio y las cookies de yt-dlp se pueden cambiar en caliente sin reiniciar.

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
| `Ctrl+T` | Login de Telegram (QR o manual) |
| `Ctrl+R` | Recargar la configuración |
| `q` | Salir |

Al pegar una URL, se abre un modal para ajustar nombre, directorio y opciones de salida; el título real se rellena en segundo plano vía la metadata del engine (o `yt-dlp --dump-single-json` como fallback) si no lo editas antes.

## Arquitectura

```
src/simple_downloader/
├── domain/           # modelos puros: DownloadRequest/Output/Context, estados, eventos
│   ├── protocols.py  # Engine, DownloadTask, HttpClient, DownloadJobRepository (Protocols)
│   ├── models.py     # dataclasses del dominio
│   └── state.py      # máquina de estados con transiciones validadas
├── engines/
│   ├── common.py     # origin()/http_with_context()/resolve_output() + resume (.resume.json)
│   ├── hls/          # engine HLS propio
│   │   ├── parser.py # parse_master / parse_media_playlist (TS + fMP4/EXT-X-MAP)
│   │   ├── fetch.py  # descarga de segmentos, AES-128, unwrap de PNG
│   │   ├── probe.py  # sniff_segment/probe_segment: clasifica el primer segmento
│   │   └── task.py   # HlsTask: descarga en paralelo, reintentos, escritura en orden
│   ├── http/         # HttpEngine + HttpDownloadTask (GET streaming con resume)
│   ├── telegram/     # TelegramEngine (Telethon): links, provider, task de 1 segmento
│   └── ytdlp/        # adapter a yt-dlp como subprocess + progreso parseado
├── app/              # DownloadManager (jobs), DownloadScheduler (workers asyncio),
│   │                 # Backend (bootstrap): arma provider, engines y config
├── db.py             # InMemoryRepository / SqliteRepository (jobs.db)
├── infra/            # AioHttpClient (get/get_range/stream), UserConfig, QR ascii
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
  3. TelegramEngine → supports: links t.me/tg:// que identifican un mensaje
  4. YtDlpEngine → catch-all: soporta cualquier URL
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

### HLS: selector de variantes

Si la master playlist tiene varias variantes, el modal de descarga ofrece un selector de resolución/bitrate (ordenado por bandwidth descendente, con etiqueta como `1080p · 2.6 Mbps`). La selección se traduce al `bandwidth` de la variante al resolver la playlist; por defecto se elige la de mayor bitrate.

### Resume de HTTP

`HttpDownloadTask` guarda un metadata `.resume.json` junto al parcial: al pausar/reiniciar, valida el archivo parcial contra la URL y continúa desde el tamaño actual (`Range: bytes=<offset>-`) en vez de reiniciar de cero. Si el servidor no responde rangos, vuelve a descargar desde el principio con aviso.

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
   (`api_id`/`api_hash` se obtienen en my.telegram.org):
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
2. Inicia sesión (dos formas equivalentes):
   - Dentro de la TUI: `Ctrl+T` abre el modal de login (QR por defecto, o
     modo manual con `[m]` para teléfono + código + 2FA — útil en Termux,
     donde el QR se superpone con la app de Telegram).
   - Por CLI (primer arranque):
     ```bash
     uv run simple-downloader --telegram-login
     ```

La sesión queda guardada en `~/.config/simple-downloader/simple_downloader.session`
(en el directorio de config se crea automáticamente, incluso si no existe).
La app se reconecta sola si la conexión MTProto se cae (p. ej. al dormir el
teléfono); si el archivo de sesión se corrompe (Android matando el proceso),
basta con borrarlo y volver a escanear el QR con `Ctrl+T`.

Después solo pegas el link en la TUI como cualquier otra URL. El nombre que
se muestra en la UI es el que reporta Telegram (metadatos del documento, que
Telethon ya obtiene al resolver el mensaje); si el documento no trae nombre,
se usa `telegram-<msg_id>.<ext>`.

### Arquitectura del módulo

```
src/simple_downloader/engines/telegram/
├── links.py    # parse_link: t.me / telegram.me / tg://
├── client.py   # TelegramClientProvider: sesión persistente, una sola conexión,
│               # reconexión automática, login QR + manual (código y 2FA)
├── task.py     # TelegramDownloadTask: 1 segmento, progreso, pausa/resume desde el parcial
└── engine.py   # TelegramEngine: resuelve el mensaje (metadatos) y descarga
```

El engine se registra tras `HttpEngine` y antes del catch-all de yt-dlp.
Los tests usan un `FakeClient` (sin red ni credenciales).

## Tests

```bash
uv run pytest            # 206 tests
uv run black src tests   # formateo
```

Cobertura de tests por módulo: parser (master/media/fMP4/init), crypt (AES-128 + PKCS7), fetch, `HlsTask` (paralelo, orden, fallos), probe (sniff/TS/fMP4/PNG/Range/desconocido), `HttpEngine` (directo + wrapper en query), resume HTTP, `TelegramEngine` (links t.me/tg://, resolución de mensaje, task 1-segmento, login QR/manual, reconexión), modal de login (cierre y flujo manual), registry, output/context, estado del job, recarga de config y persistencia.

## Estado del proyecto

- [x] TUI conectada al backend (estados, progreso, ETA, pausa/resume/cancel)
- [x] `HttpEngine` para descargas directas (path + query param) con resume
- [x] HLS propio: TS, AES-128, fMP4, PNG-wrap, referer, retries, selector de variantes
- [x] Probe de formato (TS/fMP4/PNG-wrapped/desconocido)
- [x] Output con templates y referer configurable
- [x] `TelegramEngine` (Telethon): links t.me/tg://, un solo segmento
- [x] Login de Telegram en la TUI: QR y manual (teléfono + código + 2FA)
- [x] Config en caliente (directorio, cookies de yt-dlp) sin reiniciar
- [x] Preview de metadata en el modal de descarga
- [x] Persistencia de jobs (catálogo SQLite)
- [ ] Resume de HLS propio (por ahora solo `HttpEngine`)
- [ ] Tests de integración con red real

## Visión de futuro

- **Adapter de API con FastAPI**: exponer el mismo `DownloadManager`/`EventBus`
  como servicio REST (encolar descargas, consultar estado/progreso, pausar,
  cancelar), reutilizando los engines tal cual — la UI no conoce detalles de
  los engines por diseño, así que una API HTTP sería solo otro consumidor del
  `EventBus`.
- **Resume de HLS**: aplicar el mecanismo de `.resume.json` de HTTP al
  `HlsTask` (trackear segmentos completados y reanudar desde ahí).
- **Frontend web** encima del adapter FastAPI (tablero de descargas).
- **Búsqueda dentro de canales de Telegram** (no solo descarga por link).

## Licencia

MIT — ver [LICENSE](LICENSE). Dependencias: Textual y Telethon son MIT;
yt-dlp se invoca como binario externo y mantiene su propia licencia.
