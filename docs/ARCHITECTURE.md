# Architecture

## Overview

```
 Browser (dashboard)          Terminal (pocketai)
  HTML/CSS/vanilla JS          app/terminal.py
        |   HTTP + SSE, X-PocketAI-Token   |
        +----------------+-----------------+
                         v
        FastAPI backend on 127.0.0.1:8765   (app/backend, Python 3.12 portable)
          routes/   chat, models, system, settings, files
          chat_service  prompt building, history trimming, persistence
          ai_engine     manages ONE llama-server child process
          model_manager GGUF discovery + metadata + RAM estimate
          file_analyzer read-only, sandboxed file access
          database      SQLite (database/pocketai.db)
                         |  HTTP, random port, random API key, 127.0.0.1 only
                         v
        llama-server (llama.cpp b11898, CPU build)  <- models/*.gguf
```

## Decisions

| Decision | Why |
|---|---|
| **Official `llama-server` binary** as a child process, not Python bindings | Prebuilt binaries for Windows and Ubuntu, so nothing needs compiling. It picks the best CPU backend at runtime (SSE4.2 to AVX-512). It uses the model's own chat template (`--jinja`) and separates Qwen3 "thinking" (`--reasoning-format deepseek`). Restarting it is the cleanest way to free memory and switch models. |
| **Portable Python per OS** (python.org embeddable zip on Windows, python-build-standalone on Linux) instead of PyInstaller/Nuitka | Small (Windows 22 MB, Linux 63 MB), no build step, can be updated, and the same source runs on both. A `._pth` file isolates the Windows Python from anything installed on the host. |
| **Separate `site-packages` per OS** | Compiled wheels (pydantic-core, psutil) differ per OS. Setup installs them with `pip --platform`, so one OS can prepare the other's runtime. |
| **FastAPI + Uvicorn without `[standard]` extras** | No uvloop/httptools/watchfiles: fewer native wheels, smaller, more portable. |
| **Vanilla JS + a built-in Markdown renderer/highlighter** | No frameworks or CDNs (works offline, small, auditable). Escaping all text *first* makes the renderer safe by construction, so no separate HTML sanitizer is needed. |
| **SQLite (WAL, synchronous=NORMAL)**, one write per message | Durable, no server, few flushes to the USB. Streaming tokens are never written one by one. |
| **Terminal client talks to the same HTTP API** | One code path. A second process never loads a second copy of the model. |

### Changes from the suggested structure

- Added `app/pocketai.py` (single entry point: `serve`, `chat`, `check`, `test`, `benchmark`, `selftest`), `app/terminal.py`, `app/backend/security.py`, `app/backend/routes/files.py`, and `scripts/linux-common.sh` (shared by the Linux launchers).
- Added `pocketai.bat` / `pocketai.sh` launchers for the terminal assistant.
- Frontend: `api.js`, `ui.js`, `markdown.js`, `tools.js` (Developer Tools) and `system.js` were added beside `app.js`, `chat.js`, `models.js` and `settings.js`.
- `runtime/<os>/` holds `python/`, `site-packages/` and `llama/`.

## Startup flow

1. `start-windows.bat` / `start-linux.sh` resolve their own folder (`%~dp0`, `${BASH_SOURCE[0]}`). Nothing assumes a drive letter or mount point.
2. The launcher checks the runtime exists, then runs `pocketai.py check` (Python packages, llama-server, models, free space, write access, RAM).
3. `pocketai.py serve --open` picks the configured port (or the next free one), creates a random session token, writes `database/runtime.json` (port, PID, token) so the terminal client can find it, starts Uvicorn on 127.0.0.1 and opens the browser.
4. The startup hook loads the selected model in the background, after a RAM check.
5. On exit (Ctrl+C, window closed, crash), llama-server is stopped. On Windows the child is in a Job Object with *kill-on-close*; on Linux it gets `PR_SET_PDEATHSIG`. So no orphan process keeps the model in RAM. `runtime.json` is removed. A stale one is ignored, because the PID and a health probe are checked.

## Inference flow

1. `POST /api/chat/stream {conversation_id?, message, mode}`.
2. `chat_service` validates input, creates the conversation if needed and saves the user message.
3. The prompt is the system prompt `prompts/<mode>.md` plus the newest history that fits in `ctx_size - max_tokens`. At most `history_messages` messages, estimated at 3 characters per token. History always starts with a user turn. An oversized latest message is truncated with a notice, never dropped.
4. `ai_engine.stream_chat` sends the messages to llama-server's `/v1/chat/completions` with `stream: true` and `chat_template_kwargs.enable_thinking`, and relays `content` / `reasoning_content` deltas.
5. The route runs the generator in a producer thread and forwards Server-Sent Events (`meta`, `reasoning`, `content`, `error`, `done`). If the browser disconnects, generation is cancelled. The producer still finishes, so the partial reply is always saved.
6. **Stop**: `POST /api/chat/stop` shuts down the socket to llama-server, which aborts the generation server-side. The partial reply is saved and marked `stopped`.
7. Only one generation runs at a time (CPU inference); a second request gets HTTP 409.

## Database design

```sql
conversations(id PK, title, mode, created_at, updated_at)
messages(id PK, conversation_id FK -> conversations ON DELETE CASCADE,
         role CHECK IN ('user','assistant','system'), content, reasoning,
         model, stats JSON, created_at)
meta(key PK, value)            -- schema_version
INDEX messages(conversation_id, id); INDEX conversations(updated_at DESC)
```

Search uses `LIKE` with escaped wildcards over titles and message text. That is fast enough for thousands of chats, and it needs no FTS module.

## Security model

| Threat | Protection |
|---|---|
| Other computers on the network | Uvicorn and llama-server bind to `127.0.0.1` only |
| Malicious web pages in the same browser (CSRF, DNS rebinding) | Host header must be `127.0.0.1:<port>` / `localhost:<port>`. Every `/api` call needs the per-session `X-PocketAI-Token` header (a custom header forces a CORS preflight that is never approved). A foreign `Origin` is rejected |
| Other local programs calling the model directly | llama-server uses a random port and a random `--api-key` |
| XSS through model output | All text is HTML-escaped before Markdown formatting. Only fixed tags are produced, and links are limited to http(s)/mailto. CSP `script-src 'self'`, no inline scripts, `frame-ancestors 'none'` |
| Path traversal / reading arbitrary files | Uploads never touch paths. Folder access only inside user-approved roots, with paths resolved (`realpath`) and checked to stay inside. Symlinks leaving the root are hidden and refused. Drive roots and the home folder cannot be added |
| Secrets sent to the model by accident | `.env`, keys and credential files, and content patterns (private keys, AWS/GitHub/Slack tokens, `PASSWORD=`) need an explicit per-request approval |
| Executing AI-generated code | Never happens. PocketAI has no code-execution or file-writing feature |
| Private data in logs | Logs contain events and errors only, never chat text. `logs/pocketai.log` rotates at 1 MB x 3. `llama-server.log` is overwritten on each model load |
| Tampered downloads | Python, llama.cpp and models are pinned by SHA-256 in the setup scripts |

The session token in `database/runtime.json` exists only while PocketAI runs, and it is deleted on exit. No passwords or API keys are stored anywhere. Chat history is stored unencrypted on the USB. If the drive may be lost, encrypt it (BitLocker To Go or VeraCrypt).
