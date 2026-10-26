# PocketAI USB

A private AI assistant that runs **entirely offline** from an ordinary 8 GB USB 3.0 drive on Windows 10/11 and Ubuntu (x64). The computer's CPU and RAM run the model; the USB holds the app, the model, your settings and your chat history. No cloud, no account, no GPU, no Docker, no admin rights, nothing installed on the computer.

## Features

- **Offline AI chat**: streaming replies, stop button, multi-turn conversations, Markdown with syntax-highlighted code blocks and copy buttons.
- **Chat history** saved on the USB in SQLite: search, rename, delete, continue later.
- **AI model manager**: finds any `.gguf` model in `models/`, shows size and estimated RAM, warns before loading a model that will not fit, switches/unloads models, and lets you set context length, CPU threads, temperature and maximum reply length. Includes a **low-memory mode** for 4 GB RAM computers.
- **Coding assistant**: presets for PHP, Laravel, JavaScript, TypeScript, Python, HTML, CSS, SQL and Bash (explain, generate, improve, analyze errors, find bugs, performance, documentation, explain SQL).
- **File analyzer**: read-only analysis of code files from a file picker or from project folders you allow. Detects binary files, blocks path traversal, flags `.env` files and private keys and asks before analyzing them. **Detects the installed Laravel version** and keeps suggestions compatible with it (for example Laravel 5.2).
- **System monitor**: OS, CPU, RAM, disks, USB free space, AI engine status. Updates only while open.
- **Terminal assistant** (`pocketai`) that shares models, settings and history with the dashboard.
- **Secure by default**: listens only on `127.0.0.1`, per-session token, Host/Origin checks, strict Content-Security-Policy, safe Markdown rendering, no chat text in logs.

## Requirements

| | Minimum | Recommended |
|---|---|---|
| Computer | 64-bit Intel/AMD (x86_64) | 4+ CPU cores |
| RAM | 4 GB (with Qwen3 0.6B + low-memory mode) | 8 GB+ (Qwen3 1.7B) |
| OS | Windows 10 1803+ / 11, Ubuntu 22.04+ | |
| USB | 8 GB, USB 3.0 | |
| Internet | Only once, for setup | |

## Quick start

**1. One-time setup (online).** Copy the `PocketAI-USB` folder to the USB drive, then run:

- Windows: double-click `setup-windows.bat`
- Ubuntu: `bash setup-linux.sh`

Setup downloads a portable Python, the llama.cpp engine and the Python packages (about 200 MB for both systems), verifies every file's SHA-256 hash, then **asks** which AI model to download (Qwen3 1.7B, about 1.1 GB, is recommended). It finishes with a short test inference.

**2. Everyday use (offline).**

- Windows: double-click `start-windows.bat`
- Ubuntu: `bash start-linux.sh`

The dashboard opens in your browser at `http://127.0.0.1:8765/` (or the next free port). Keep the launcher window open, and press `Ctrl+C` in it to quit.

**Terminal assistant:** `pocketai.bat` (Windows) or `bash pocketai.sh` (Ubuntu).

## Documentation

- [Installation](docs/INSTALLATION.md): Windows, Ubuntu, preparing the USB, models
- [User guide](docs/USER_GUIDE.md): chat, models, coding assistant, file analyzer, terminal commands
- [Architecture](docs/ARCHITECTURE.md): design, inference flow, database, security
- [Performance](docs/PERFORMANCE.md): storage, memory, benchmarks, tuning
- [Troubleshooting](docs/TROUBLESHOOTING.md)

## Licenses

PocketAI's own code is yours to use. Bundled or downloaded components keep their licenses: llama.cpp (MIT), Python (PSF), FastAPI / Starlette / Uvicorn / Pydantic (MIT/BSD), psutil (BSD). Each model has its own license: Qwen3 (Apache-2.0), Llama 3.2 (Llama 3.2 Community License), Gemma 3 (Gemma Terms of Use). Setup shows the license before downloading.
"# PocketAI-USB" 
