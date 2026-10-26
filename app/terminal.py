"""Terminal AI assistant.

Talks to the same local backend as the dashboard (same model, settings and chat history).
If the dashboard is not running, a headless backend is started inside this process and
stopped again on exit.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Iterator

from backend import __version__

HELP = """Commands:
  /help              show this help
  /models            list available models
  /model [N|name]    show the active model, or switch to model N / by name
  /mode [name]       show or change the assistant mode (general, coding, debugging, documentation)
  /new               start a new conversation
  /history [N]       list recent conversations, or continue conversation N
  /system            show system information
  /clear             clear the screen
  /exit              quit (also Ctrl+D / Ctrl+Z)
Press Ctrl+C while the AI is answering to stop the answer."""


class Style:
    def __init__(self) -> None:
        self.on = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
        if self.on and os.name == "nt":
            os.system("")                       # enables ANSI escape handling in conhost

    def __call__(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.on else text


S = Style()


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: Any):
        super().__init__(detail if isinstance(detail, str) else json.dumps(detail))
        self.status = status
        self.detail = detail


class Client:
    def __init__(self, port: int, token: str) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.token = token

    def _req(self, method: str, path: str, body: Any = None) -> urllib.request.Request:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("X-PocketAI-Token", self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        return req

    def call(self, method: str, path: str, body: Any = None, timeout: float = 60) -> Any:
        try:
            with urllib.request.urlopen(self._req(method, path, body), timeout=timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail")
            except Exception:
                detail = e.reason
            raise ApiError(e.code, detail) from None

    def stream(self, body: dict) -> Iterator[dict]:
        try:
            resp = urllib.request.urlopen(self._req("POST", "/api/chat/stream", body), timeout=900)
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail")
            except Exception:
                detail = e.reason
            raise ApiError(e.code, detail) from None
        with resp:
            event = ""
            for raw in resp:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:") and event:
                    yield json.loads(line[5:])


def connect() -> tuple[Client, Any]:
    """Use a running backend if there is one, else start one in this process."""
    from pocketai import BackgroundServer, read_runtime_state

    state = read_runtime_state()
    if state:
        return Client(state["port"], state["token"]), None
    srv = BackgroundServer()
    srv.start_thread()
    if not srv.wait_started():
        raise RuntimeError("the local backend did not start; see logs/pocketai.log")
    autoload = getattr(srv.app.state, "autoload_thread", None)
    if autoload is not None:
        autoload.join(timeout=30)          # model load has been started (or skipped) after this
    return Client(srv.port, srv.token.value), srv


def wait_for_model(client: Client) -> dict:
    st = client.call("GET", "/api/models/status")
    if st["state"] == "loading":
        print(S("2", "Loading model"), end="", flush=True)
        while st["state"] == "loading":
            time.sleep(0.5)
            print(S("2", "."), end="", flush=True)
            st = client.call("GET", "/api/models/status")
        print()
    return st


def model_label(client: Client, mid: str) -> str:
    for m in client.call("GET", "/api/models")["models"]:
        if m["id"] == mid:
            return m["display_name"] or m["file"]
    return mid or "none"


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PB"


class Terminal:
    def __init__(self, client: Client) -> None:
        self.c = client
        self.conv_id: int | None = None
        self.mode = "general"

    # ---------------------------------------------------------------- commands
    def banner(self) -> None:
        st = wait_for_model(self.c)
        state = {"ready": "Ready", "stopped": "No model loaded", "error": "Error"}.get(st["state"], st["state"])
        print(S("1", f"PocketAI USB v{__version__}"))
        print()
        print(f"Model: {model_label(self.c, st['model']) if st['model'] else 'none'}")
        print("Mode: Offline")
        print(f"Status: {state}")
        if st.get("error"):
            print(S("33", f"  {st['error']}"))
        if st["state"] != "ready":
            print(S("33", "Use /models and /model N to load a model."))
        print(S("2", "Type /help for commands."))
        print()

    def cmd_models(self, _arg: str) -> None:
        data = self.c.call("GET", "/api/models")
        if not data["models"]:
            print(f"No models found. Copy a .gguf file into {data['models_dir']} or run the setup script.")
            return
        for i, m in enumerate(data["models"], 1):
            active = "*" if m["id"] == data["status"]["model"] else " "
            warn = "" if m.get("fits_in_ram", True) else S("33", "  (may not fit in free RAM)")
            print(f" {active}{i}. {m['display_name']}  [{m['file']}, {fmt_bytes(m['size_bytes'])}]{warn}")

    def cmd_model(self, arg: str) -> None:
        data = self.c.call("GET", "/api/models")
        if not arg:
            st = data["status"]
            print(f"Active model: {model_label(self.c, st['model']) if st['model'] else 'none'} ({st['state']})")
            return
        models = data["models"]
        target = None
        if arg.isdigit() and 1 <= int(arg) <= len(models):
            target = models[int(arg) - 1]
        else:
            matches = [m for m in models if arg.lower() in (m["file"] + m["display_name"]).lower()]
            target = matches[0] if len(matches) == 1 else None
        if not target:
            print("No such model. Use /models to list them.")
            return
        body = {"model": target["id"], "force": False}
        try:
            self.c.call("POST", "/api/models/load", body)
        except ApiError as e:
            if e.status == 409 and isinstance(e.detail, str) and "RAM" in e.detail:
                print(S("33", e.detail))
                if input("Load anyway? [y/N] ").strip().lower() != "y":
                    return
                body["force"] = True
                self.c.call("POST", "/api/models/load", body)
            else:
                raise
        st = wait_for_model(self.c)
        print(f"Model: {target['display_name']} - {st['state']}" + (f" ({st['error']})" if st.get("error") else ""))

    def cmd_mode(self, arg: str) -> None:
        modes = [m["id"] for m in self.c.call("GET", "/api/modes")["modes"]]
        if not arg:
            print(f"Mode: {self.mode}  (available: {', '.join(modes)})")
        elif arg in modes:
            self.mode = arg
            print(f"Mode set to {arg}.")
        else:
            print(f"Unknown mode. Available: {', '.join(modes)}")

    def cmd_new(self, _arg: str) -> None:
        self.conv_id = None
        print("Started a new conversation.")

    def cmd_history(self, arg: str) -> None:
        convs = self.c.call("GET", "/api/conversations?limit=15")["conversations"]
        if arg.isdigit():
            idx = int(arg)
            if not 1 <= idx <= len(convs):
                print("No such conversation.")
                return
            conv = self.c.call("GET", f"/api/conversations/{convs[idx - 1]['id']}")
            self.conv_id, self.mode = conv["id"], conv["mode"]
            print(S("1", f"== {conv['title']} =="))
            for m in conv["messages"][-6:]:
                who = S("36", "You") if m["role"] == "user" else S("32", "AI")
                text = m["content"] if len(m["content"]) < 600 else m["content"][:600] + " ..."
                print(f"{who}: {text}\n")
            print(S("2", "(continuing this conversation)"))
            return
        if not convs:
            print("No saved conversations yet.")
        for i, cv in enumerate(convs, 1):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(cv["updated_at"]))
            print(f" {i:>2}. {cv['title']}  {S('2', when)}")
        print(S("2", "Use /history N to continue one."))

    def cmd_system(self, _arg: str) -> None:
        s = self.c.call("GET", "/api/system")
        e = s["engine"]
        print(f"OS:        {s['os']} ({s['arch']})")
        print(f"CPU:       {s['cpu']['name']} - {s['cpu']['physical_cores']} cores / "
              f"{s['cpu']['logical_cores']} threads, {s['cpu']['percent']}% used")
        print(f"RAM:       {fmt_bytes(s['memory']['available'])} free of {fmt_bytes(s['memory']['total'])}")
        print(f"App drive: {fmt_bytes(s['app_drive']['free'])} free of {fmt_bytes(s['app_drive']['total'])}")
        print(f"AI model:  {e['model'] or 'none'} ({e['state']}"
              + (f", {fmt_bytes(e['rss_bytes'])} RAM" if e.get("rss_bytes") else "") + ")")

    def cmd_clear(self, _arg: str) -> None:
        os.system("cls" if os.name == "nt" else "clear")

    # ---------------------------------------------------------------- chat
    def ask(self, text: str) -> None:
        body = {"message": text, "mode": self.mode}
        if self.conv_id:
            body["conversation_id"] = self.conv_id
        width = shutil.get_terminal_size((80, 20)).columns
        printed_ai = False
        in_reasoning = False
        try:
            for ev in self.c.stream(body):
                t = ev["type"]
                if t == "meta":
                    self.conv_id = ev["conversation_id"]
                elif t == "reasoning":
                    if not in_reasoning:
                        print(S("2", "(thinking) "), end="")
                        in_reasoning = True
                    print(S("2", ev["text"]), end="", flush=True)
                elif t == "content":
                    if not printed_ai:
                        if in_reasoning:
                            print()
                        print("\n" + S("32", "AI: "), end="")
                        printed_ai = True
                    print(ev["text"], end="", flush=True)
                elif t == "error":
                    print("\n" + S("31", f"Error: {ev['message']}"))
                elif t == "done":
                    stats = ev.get("stats") or {}
                    note = " (stopped)" if ev.get("stopped") else ""
                    tps = f"{stats['tokens_per_s']} tok/s" if stats.get("tokens_per_s") else ""
                    print()
                    if tps or note:
                        print(S("2", f"[{tps}{note}]"))
        except KeyboardInterrupt:
            self.c.call("POST", "/api/chat/stop", {})
            print("\n" + S("33", "[stopped]"))
        print("-" * min(width, 60))

    def loop(self) -> None:
        commands = {"/models": self.cmd_models, "/model": self.cmd_model, "/mode": self.cmd_mode,
                    "/new": self.cmd_new, "/history": self.cmd_history, "/system": self.cmd_system,
                    "/clear": self.cmd_clear}
        self.banner()
        while True:
            try:
                text = input(S("36", "You: ")).replace("\ufeff", "").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not text:
                continue
            if text.startswith("/"):
                cmd, _, arg = text.partition(" ")
                cmd = cmd.lower()
                if cmd in ("/exit", "/quit"):
                    return
                if cmd == "/help":
                    print(HELP)
                    continue
                fn = commands.get(cmd)
                if not fn:
                    print("Unknown command. Type /help.")
                    continue
                try:
                    fn(arg.strip())
                except ApiError as e:
                    print(S("31", f"Error: {e}"))
                continue
            try:
                self.ask(text)
            except ApiError as e:
                print(S("31", f"Error: {e}"))


def run_terminal() -> int:
    try:
        client, srv = connect()
    except Exception as exc:
        print(f"Could not start PocketAI: {exc}")
        return 1
    try:
        Terminal(client).loop()
    finally:
        if srv is not None:
            print("Stopping the local AI engine ...")
            srv.stop()
            time.sleep(0.5)
            from backend.ai_engine import engine
            engine.unload()
    return 0
