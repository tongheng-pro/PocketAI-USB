# Installation

Setup is the **only** step that needs the internet. After it finishes, PocketAI runs fully offline on every computer you plug the drive into.

## 1. Prepare the USB drive

- Use an 8 GB (or larger) USB 3.0 drive, plugged into a USB 3 (blue) port for speed.
- **File system:**
  - **NTFS**: best on Windows, and readable/writable on Ubuntu 22.04+.
  - **exFAT**: works on both. PocketAI never relies on symlinks or Unix permission bits, so exFAT is supported. (exFAT on Ubuntu has not been tested on real hardware yet; see Known limitations.)
  - FAT32 is **not** suitable: the 1.1 GB model is fine, but larger models exceed FAT32's 4 GB file limit.
- PocketAI never formats or erases a drive. If you need to reformat, back up first and use your OS's own tools.
- Copy the whole `PocketAI-USB` folder to the drive. Any drive letter, mount point or folder name works, including names with spaces.

## 2. Windows 10/11 (x64)

1. Open the `PocketAI-USB` folder on the USB drive.
2. Double-click **`setup-windows.bat`**. If Windows SmartScreen warns about an unknown script, choose *More info, then Run anyway*.
3. Setup will:
   1. check the OS and CPU (64-bit Intel/AMD),
   2. download the official Python 3.12 embeddable package (11 MB, SHA-256 verified) into `runtime\windows\python`,
   3. download llama.cpp build b11898 for Windows CPU (SHA-256 verified) into `runtime\windows\llama`,
   4. install FastAPI, Uvicorn, Pydantic and psutil into `runtime\windows\site-packages`,
   5. create `database\pocketai.db` and `config\settings.json`,
   6. **ask** which model to download (nothing is downloaded without your answer),
   7. run a test inference and print a storage report.
4. Start PocketAI with **`start-windows.bat`**.

Nothing is installed on the computer. No administrator rights are needed. `curl.exe` and `tar.exe` (built into Windows 10 1803 and later) are used for the first download.

Useful options (run from a terminal in the folder):

```bat
setup-windows.bat --also-other-os
setup-windows.bat --model qwen3-0.6b
setup-windows.bat --no-models --skip-test
```

`--also-other-os` also prepares the Ubuntu runtime from Windows, so the drive works on both systems without running setup on Ubuntu.

## 3. Ubuntu 22.04+ (x64)

1. Open a terminal in the `PocketAI-USB` folder on the drive (in Files: right-click, *Open in Terminal*).
2. Run:
   ```bash
   bash setup-linux.sh
   ```
   Always start the scripts with `bash`: USB drives often do not keep the "executable" permission.
3. Setup downloads a portable Python 3.12 (python-build-standalone, SHA-256 verified) into `runtime/linux/python`, then runs the same steps as on Windows.
4. Start PocketAI:
   ```bash
   bash start-linux.sh
   ```

System libraries used by llama.cpp that come with a normal Ubuntu desktop: `libstdc++6`, `libgomp1`, `libssl3`. If the launcher reports a missing library:

```bash
sudo apt install libgomp1 libssl3
```

If the drive is mounted with `noexec`, the launcher copies the runtime (about 120 MB) once to `~/.cache/pocketai/` and runs it from there. Your models, chats and settings stay on the USB.

## 4. Models

PocketAI works with any chat model in **GGUF** format. Setup offers these (sizes and hashes verified against Hugging Face):

| Model | File | Size | RAM needed | License |
|---|---|---|---|---|
| **Qwen3 1.7B** (default) | `Qwen3-1.7B-Q4_K_M.gguf` (unsloth) | 1.1 GB | 8 GB PC | Apache-2.0 |
| Qwen3 0.6B | `Qwen3-0.6B-Q4_K_M.gguf` (unsloth) | 397 MB | 4 GB PC | Apache-2.0 |
| Llama 3.2 1B Instruct | `Llama-3.2-1B-Instruct-Q4_K_M.gguf` (bartowski) | 808 MB | 4 GB PC | Llama 3.2 Community License |
| Gemma 3 1B Instruct | `gemma-3-1b-it-Q4_K_M.gguf` (ggml-org) | 806 MB | 4 GB PC | Gemma Terms of Use |

To add a model later, either run setup again (`setup-windows.bat --model llama3.2-1b`, `bash setup-linux.sh --model gemma3-1b`) or copy any `.gguf` file into `models/` and press **Rescan models** in the dashboard. Vision projector files (`mmproj-*.gguf`) are ignored.

## 5. Moving to another computer

Unplug the drive (use *Safely remove* / *Eject* first) and plug it into any other supported computer. Run `start-windows.bat` or `bash start-linux.sh`. No setup is needed again, as long as the runtime for that operating system was prepared (run setup once per OS, or use `--also-other-os`).

## 6. Updating or uninstalling

- To update the engine or packages, edit the pinned versions in `scripts/setup.py` / `requirements.txt` and run setup again. Setup asks before replacing an existing engine.
- To uninstall, delete the `PocketAI-USB` folder. Nothing was installed on any computer. On Ubuntu, a `noexec` cache may exist in `~/.cache/pocketai/`.
