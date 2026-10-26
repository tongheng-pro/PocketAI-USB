# User guide

## Chat

- Type in the box at the bottom. **Enter** sends; **Shift+Enter** adds a new line.
- The reply appears word by word. Press **Stop** (or **Esc**) to stop it. The part already written is kept and saved.
- **Mode** (next to the input) sets the assistant's expertise: *General*, *Coding*, *Debugging* or *Documentation*. Each conversation remembers its mode.
- Under each reply: **Copy** copies the whole answer as Markdown. Every code block has its own **Copy** button. The small text shows speed (tokens per second) and length.
- If a model "thinks" (Qwen3 with *Show model thinking* switched on), the reasoning appears in a collapsible **Thinking** box above the answer.

### Chat history

All conversations are saved on the USB (`database/pocketai.db`).

- **Search** (Ctrl+K) searches titles *and* message text.
- Hover over a chat in the sidebar to **rename** or **delete** it.
- **New chat**: the button at the top, or Ctrl+Shift+O.

Long conversations are not a problem: only the most recent messages that fit the model's context window are sent to the model. Older ones stay saved, but the model no longer "sees" them.

## AI Models

Open **AI Models** in the sidebar (or click the model name at the top right).

- Every `.gguf` file in `models/` is listed with its size, quantization, and an **estimate of the RAM it needs**. A yellow badge means it needs more than is currently free.
- **Load** starts the model. Loading from USB takes a few seconds (USB 3) up to a minute (USB 2). Only one model is loaded at a time.
- If a model probably does not fit in free memory, PocketAI warns you first. You can close other programs, pick a smaller model, lower the context length, or *Load anyway*.
- **Unload model** frees its memory.
- **Rescan models** finds files you just copied into `models/`.

### Generation settings

| Setting | What it does | Tip |
|---|---|---|
| Context length | How much text (prompt + history + reply) the model can see at once | 4096 is a good default. 2048 saves about 300 MB RAM on Qwen3 1.7B |
| CPU threads | Cores used for inference | 0 = automatic (physical cores) |
| Temperature | Randomness | 0.2 to 0.4 for code, 0.7 for chat |
| Top-p | Sampling cut-off | Leave at 0.9 |
| Max output tokens | Longest single reply | 1024 |
| Low-memory mode | Caps context at 2048, history at 8 messages, replies at 512 tokens, smaller batches | Use on 4 GB RAM computers |
| Show model thinking | Qwen3 reasoning mode | Better on hard problems but **much** slower on a CPU |

Context length, threads and low-memory mode apply when the model is (re)loaded. PocketAI offers to reload for you.

## Developer Tools

### Coding assistant

1. Choose the **language** (PHP, Laravel, JavaScript, TypeScript, Python, HTML, CSS, SQL, Bash) and the **task**: explain code, generate code, suggest improvements, analyze an error message, find bugs, performance review, write documentation, explain a database query.
2. For **Laravel**, enter the Laravel and PHP versions (look in `composer.lock`). PocketAI tells the model to use only APIs that exist in that version. This matters for legacy projects such as Laravel 5.2.
3. Paste your code or error and press **Ask in chat**. A new conversation opens with the matching expert prompt.

PocketAI never runs code and never changes your files. Review suggestions before using them: small offline models make mistakes.

### File analyzer

- **Analyze a file**: pick a text file (`.php .js .ts .py .html .css .json .md .sql .txt` and a few more; up to 256 KB by default). PocketAI shows its size, line count and type, warns about problems, then sends it to a new chat with the task you choose.
- **Browse a project folder**: first add the folder under **Settings, Project folders**. Then browse it here and click a file to analyze it. You cannot leave the folder (`..` and links pointing outside are blocked), and whole drives or your home folder cannot be added.
- **Laravel detection**: for a project folder, PocketAI reads the installed version from `vendor/laravel/framework` (or `composer.lock`) and adds it, plus the PHP requirement, to the request so suggestions stay compatible.
- **Sensitive files**: `.env` files, private keys, credential files and code with hard-coded passwords or tokens are flagged. PocketAI asks for confirmation every time before analyzing them. Their content then goes only to the local model and into your chat history on the USB.
- Files that are binary, too large or of an unsupported type are refused. Only the beginning of a long file is sent if it does not fit the context window, and you are told when that happens.

## System Monitor

Shows the operating system, CPU model, CPU usage, total and free RAM, all disks, the USB's free space, the active model, its memory use and whether it is generating. It refreshes every 3 seconds **only while the page is open and visible**. **Measure** calculates how much space each PocketAI folder uses.

## Settings

Theme, dashboard port, how many messages each chat remembers, maximum file size for analysis, auto-loading the model at startup, and the project folders the file analyzer may read. It also shows where your data is stored and the keyboard shortcuts.

## Keyboard shortcuts

| Keys | Action |
|---|---|
| Enter / Shift+Enter | Send / new line |
| Esc | Stop generating |
| Ctrl+Shift+O | New chat |
| Ctrl+K | Search chat history |
| Ctrl+B | Show/hide sidebar |
| Ctrl+Shift+L | Dark/light mode |
| Alt+1 to Alt+5 | Chat / Models / Developer Tools / System / Settings |
| Ctrl+/ | Show shortcuts |

## Terminal assistant

Start it with `pocketai.bat` (Windows) or `bash pocketai.sh` (Ubuntu). It uses the same models, settings and chat history as the dashboard. If the dashboard is running, it connects to it; otherwise it starts its own engine and stops it again on exit.

```
PocketAI USB v1.0.0

Model: Qwen3-1.7B
Mode: Offline
Status: Ready

You: Explain Laravel middleware.

AI: Laravel middleware allows you to ...
```

| Command | Action |
|---|---|
| `/help` | List commands |
| `/models` | List models (`*` = active) |
| `/model` | Show the active model |
| `/model N` or `/model name` | Switch to model N from `/models`, or by part of its name |
| `/mode [name]` | Show or change the mode: general, coding, debugging, documentation |
| `/new` | Start a new conversation |
| `/history` | List recent conversations |
| `/history N` | Continue conversation N |
| `/system` | System information |
| `/clear` | Clear the screen |
| `/exit` | Quit (also Ctrl+D, or Ctrl+Z then Enter on Windows) |

Press **Ctrl+C** while the AI is answering to stop the answer.

Other commands:

```bash
bash pocketai.sh check        # pre-flight checks
bash pocketai.sh test         # one quick test inference
bash pocketai.sh benchmark    # speed test, saved to logs/benchmark.csv
bash pocketai.sh selftest     # run the automated tests
```

(On Windows, use `pocketai.bat check`, and so on.)

To type just `pocketai` on Ubuntu, add an alias to `~/.bashrc`, adjusting the path to your drive:

```bash
alias pocketai='bash "/media/$USER/DATA/PocketAI-USB/pocketai.sh"'
```
