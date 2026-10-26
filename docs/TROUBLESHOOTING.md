# Troubleshooting

Start with the built-in checks; they explain most problems:

```bash
pocketai.bat check            # Windows
bash pocketai.sh check        # Ubuntu
```

Logs are in `logs/`: `pocketai.log` (backend) and `llama-server.log` (AI engine, rewritten each time a model loads). They never contain your chat text.

## Missing model

*"No AI model found"*, or *"No model is loaded"*.

- Run the setup script and choose a model, or copy any `.gguf` chat model into the `models` folder, then press **AI Models, Rescan models**.
- A model marked *unreadable GGUF header* is damaged or incomplete (for example an interrupted copy). Copy it again, or delete it and re-download. Setup verifies the SHA-256 hash.
- `.part` files are unfinished downloads. Run setup again to resume.

## Missing runtime

*"The Windows runtime is missing"*, *"The Linux runtime is missing"*, *"llama-server missing"* or *"missing Python package"*.

- Run `setup-windows.bat` or `bash setup-linux.sh` once while online. A drive prepared only on Windows needs `setup-windows.bat --also-other-os` (or setup on Ubuntu) before it works on Ubuntu, and the other way round.
- If an antivirus quarantined `llama-server.exe` or its DLLs, restore them or allow the `PocketAI-USB\runtime` folder, then run setup again.

## Insufficient RAM

*"... needs about X GB RAM ... only Y GB is available"*.

- Close browsers with many tabs, IDEs and other heavy programs.
- Lower the **context length** (AI Models, Generation settings), or turn on **low-memory mode**.
- Use a smaller model: Qwen3 0.6B needs about 1 GB.
- *Load anyway* may work thanks to swap/pagefile, but it can be very slow.
- If the engine stops with *"llama-server exited unexpectedly"*, it most likely ran out of memory. Check `logs/llama-server.log`.

## Slow inference

- See [PERFORMANCE.md](PERFORMANCE.md). Expect roughly 5 to 15 tokens/s for Qwen3 1.7B on a typical 4-core laptop.
- Turn **off** *Show model thinking*: thinking can add hundreds of tokens before the answer.
- Plug laptops into power; battery-saver modes throttle the CPU.
- A slow *first* load usually means a USB 2 port or a slow drive. Later loads are faster because the OS caches the file.

## USB permission errors

- *"cannot write to ... database"*: the drive is write-protected (check for a lock switch), full, or mounted read-only. On Windows, check the drive in Explorer. On Ubuntu, `mount | grep media` shows `ro`. Repair with `sudo fsck` (exFAT/NTFS) or on Windows with `chkdsk X: /f`, after backing up.
- **Ubuntu `Permission denied` when starting**: always use `bash start-linux.sh`, not `./start-linux.sh`. If the drive is mounted `noexec`, the launcher automatically copies the runtime to `~/.cache/pocketai` and runs it from there.
- **NTFS drive read-only on Ubuntu**: Windows "Fast Startup" or hibernation leaves NTFS marked as in use. On Windows, shut down fully (Shift+Shutdown) or disable Fast Startup, then re-plug the drive. Also *Safely remove* the drive before unplugging.

## Windows compatibility problems

- **Windows SmartScreen / "Windows protected your PC"**: choose *More info, then Run anyway* (the scripts are plain text, so you can read them first).
- **`curl.exe` or `tar.exe` not found during setup**: Windows 10 older than version 1803. Update Windows, or run setup on another PC: the prepared drive works on older Windows 10 too.
- **The browser does not open**: open the address printed in the launcher window (for example `http://127.0.0.1:8765/`) yourself.
- **"Port in use"**: PocketAI automatically uses the next free port and prints the address. To choose a fixed one, change *Dashboard port* in Settings.
- **The launcher window closes immediately**: open `cmd`, `cd` to the folder, run `start-windows.bat`, and read the message.
- **Windows on ARM** is not supported (x64 binaries only).

## Ubuntu dependency problems

- **Missing libraries** (`libgomp.so.1`, `libssl.so.3`): `sudo apt install libgomp1 libssl3`. The launcher lists any missing library at startup.
- **Ubuntu 20.04 or older** lacks OpenSSL 3, which the llama.cpp Ubuntu build needs. Use Ubuntu 22.04 or newer.
- **`GLIBC_x.yy not found`**: the distribution is too old for the prebuilt binaries. Use Ubuntu 22.04+.
- **The browser does not open**: `xdg-open` is missing (minimal installs), so open the printed address manually.
- **exFAT drive not mounting**: on Ubuntu 22.04+ exFAT is supported by the kernel. On older systems, install `exfatprogs`.

## Something else

- Delete `config/settings.json` to reset all settings to defaults; your chats are not affected.
- `database/runtime.json` left over after a crash is harmless; it is ignored and replaced.
- Run the automated tests with `pocketai.bat selftest` / `bash pocketai.sh selftest`.
