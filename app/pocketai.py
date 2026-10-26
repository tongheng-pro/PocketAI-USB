"""PocketAI USB entry point.

    python app/pocketai.py serve [--open] [--port N] [--no-autoload]
    python app/pocketai.py chat                    # terminal assistant
    python app/pocketai.py check                   # pre-flight checks used by the launchers
    python app/pocketai.py test [--model ID]       # load a model and run one short inference
    python app/pocketai.py benchmark               # load time + tokens/s, saved to logs/benchmark.csv
    python app/pocketai.py selftest [-v]           # automated tests

The launchers (start-windows.bat, start-linux.sh, pocketai.bat, pocketai.sh) call this with
the portable Python from runtime/<os>/.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # embeddable Python skips the script dir

from backend import __version__, config  # noqa: E402

MIN_FREE_FATAL = 50 * 1024**2
MIN_FREE_WARN = 500 * 1024**2


# ------------------------------------------------------------------------- runtime state

def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return False


def read_runtime_state() -> dict | None:
    """Info about an already running backend (port + session token), if it is alive."""
    try:
        state = json.loads(config.RUNTIME_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not _pid_alive(int(state.get("pid", 0))):
        return None
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{state['port']}/api/health", timeout=2) as r:
            if r.status == 200:
                return state
    except (OSError, KeyError, ValueError):
        pass
    return None


def write_runtime_state(port: int, token: str) -> None:
    config.DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    data = {"port": port, "pid": os.getpid(), "token": token, "url": f"http://127.0.0.1:{port}/",
            "started": time.time()}
    tmp = config.RUNTIME_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)       # ignored on FAT/exFAT/NTFS, effective on Linux filesystems
    except OSError:
        pass
    os.replace(tmp, config.RUNTIME_STATE_FILE)


def clear_runtime_state() -> None:
    try:
        state = json.loads(config.RUNTIME_STATE_FILE.read_text(encoding="utf-8"))
        if state.get("pid") == os.getpid():
            config.RUNTIME_STATE_FILE.unlink()
    except (OSError, json.JSONDecodeError):
        pass


def pick_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    from backend.ai_engine import free_port
    return free_port()


# ------------------------------------------------------------------------------- serve

class BackgroundServer:
    """Runs uvicorn in-process (used by `serve` in the main thread and `chat` in a thread)."""

    def __init__(self, port: int | None = None, autoload: bool = True) -> None:
        import uvicorn
        from backend.main import ServerState, create_app, setup_logging
        from backend.security import SessionToken

        setup_logging()
        settings = config.load_settings()
        self.port = pick_port(port or settings["port"])
        self.token = SessionToken()
        self.app = create_app(ServerState(self.port, self.token), autoload=autoload)
        cfg = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning",
                             access_log=False, lifespan="on", timeout_graceful_shutdown=3)
        self.server = uvicorn.Server(cfg)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def wait_started(self, timeout: float = 30) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.server.started:
                return True
            time.sleep(0.05)
        return False

    def run(self) -> None:
        write_runtime_state(self.port, self.token.value)
        try:
            self.server.run()
        finally:
            clear_runtime_state()
            from backend.ai_engine import engine
            engine.unload()

    def start_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True, name="uvicorn")
        t.start()
        return t

    def stop(self) -> None:
        self.server.should_exit = True


def cmd_serve(args: argparse.Namespace) -> int:
    running = read_runtime_state()
    if running:
        print(f"PocketAI is already running at {running['url']}")
        if args.open:
            webbrowser.open(running["url"])
        return 0
    srv = BackgroundServer(args.port, autoload=not args.no_autoload)
    print(f"PocketAI USB v{__version__}")
    print(f"Dashboard: {srv.url}")
    print("Keep this window open while you use PocketAI. Press Ctrl+C to stop.")
    if args.open:
        def opener() -> None:
            if srv.wait_started():
                webbrowser.open(srv.url)
        threading.Thread(target=opener, daemon=True).start()
    try:
        srv.run()
    except KeyboardInterrupt:
        pass
    print("PocketAI stopped.")
    return 0


# ------------------------------------------------------------------------------- check

def cmd_check(args: argparse.Namespace) -> int:
    """Pre-flight checks. Exit 1 only for problems that stop the backend from starting."""
    ok = True

    def line(level: str, msg: str) -> None:
        print(f"  [{level}] {msg}")

    print(f"PocketAI USB v{__version__} - checking {config.APP_DIR.parent}")
    line("ok", f"Python {sys.version.split()[0]} ({config.os_name()} {config.machine_arch()})")
    if config.machine_arch() != "x86_64":
        line("!!", f"unsupported CPU architecture {config.machine_arch()} (x86_64 required)")
        ok = False
    try:
        import fastapi  # noqa: F401
        import psutil
        import uvicorn  # noqa: F401
        line("ok", "Python packages present")
    except ImportError as exc:
        line("!!", f"missing Python package ({exc.name}). Run the setup script for this OS.")
        return 1

    binary = config.llama_server_path()
    if binary.is_file():
        line("ok", f"llama-server found ({binary.parent.name}/{binary.name})")
    else:
        line("warn", f"llama-server missing at {binary} - run setup; chat will not work until then")

    from backend import model_manager
    models = [m for m in model_manager.discover() if m["valid"]]
    active = config.load_settings()["active_model"]
    if not models:
        line("warn", f"no GGUF models in {config.MODELS_DIR} - run setup to download one, or copy a .gguf file there")
    elif active and not any(m["id"] == active for m in models):
        line("warn", f"selected model '{active}' not found; the first available model will be used")
    else:
        line("ok", f"{len(models)} model(s) found" + (f", selected: {active}" if active else ""))

    free = shutil.disk_usage(config.ROOT).free
    if free < MIN_FREE_FATAL:
        line("!!", f"only {free / 2**20:.0f} MB free on the drive - free some space first")
        ok = False
    elif free < MIN_FREE_WARN:
        line("warn", f"low free space on the drive: {free / 2**20:.0f} MB")
    else:
        line("ok", f"{free / 2**30:.1f} GB free on the drive")

    vm = psutil.virtual_memory()
    line("ok" if vm.available > 2 * 2**30 else "warn",
         f"RAM: {vm.available / 2**30:.1f} GB available of {vm.total / 2**30:.1f} GB")
    try:
        config.ensure_dirs()
        probe = config.DATABASE_DIR / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        line("ok", "drive is writable")
    except OSError as exc:
        line("!!", f"cannot write to {config.DATABASE_DIR}: {exc} (is the USB write-protected?)")
        ok = False
    return 0 if ok else 1


# -------------------------------------------------------------------------------- test

def cmd_test(args: argparse.Namespace) -> int:
    """Load a model directly and run one short prompt (used by the setup scripts)."""
    from backend import model_manager
    from backend.ai_engine import EngineError, MemoryWarning, engine

    settings = config.load_settings()
    mid = args.model or settings["active_model"]
    if not mid:
        models = [m for m in model_manager.discover() if m["valid"]]
        if not models:
            print("No model available to test.")
            return 1
        mid = models[0]["id"]
    test_settings = dict(settings, ctx_size=min(settings["ctx_size"], 2048))
    print(f"Loading {mid} ...")
    t0 = time.time()
    try:
        engine.load(mid, test_settings, force=args.force, wait=True)
    except MemoryWarning as exc:
        print(f"Memory warning: {exc}\nRe-run with --force to try anyway.")
        return 1
    except (EngineError, FileNotFoundError) as exc:
        print(f"Load failed: {exc}")
        return 1
    print(f"Loaded in {time.time() - t0:.1f}s. Generating ...")
    try:
        params = dict(config.effective(test_settings), max_tokens=64, temperature=0.2,
                      enable_thinking=False)
        out = []
        stats = {}
        for ev in engine.stream_chat([{"role": "user", "content": args.prompt}], params):
            if ev["type"] == "content":
                out.append(ev["text"])
                print(ev["text"], end="", flush=True)
            elif ev["type"] == "done":
                stats = ev["stats"]
        print()
        print(f"Result: {stats.get('completion_tokens')} tokens, "
              f"{stats.get('tokens_per_s')} tokens/s generation, "
              f"{stats.get('prompt_tokens_per_s')} tokens/s prompt, "
              f"first token after {stats.get('time_to_first_token_s')}s")
        if not "".join(out).strip():
            print("The model produced no text.")
            return 1
        return 0
    except EngineError as exc:
        print(f"Inference failed: {exc}")
        return 1
    finally:
        engine.unload()


# -------------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pocketai", description="PocketAI USB - offline AI assistant")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="start the web dashboard")
    s.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--no-autoload", action="store_true", help="do not load a model at startup")
    sub.add_parser("chat", help="terminal assistant")
    sub.add_parser("check", help="pre-flight checks")
    t = sub.add_parser("test", help="run one test inference")
    t.add_argument("--model", default="")
    t.add_argument("--force", action="store_true", help="ignore the memory warning")
    t.add_argument("--prompt", default="In one sentence, what is a USB flash drive?")
    sub.add_parser("version")
    sub.add_parser("benchmark", help="measure load time and tokens/s (extra args go to the script)",
                   add_help=False)
    sub.add_parser("selftest", help="run the automated test suite", add_help=False)
    args, extra = p.parse_known_args(argv)
    if extra and args.cmd not in ("benchmark", "selftest"):
        p.error(f"unrecognized arguments: {' '.join(extra)}")

    root = Path(__file__).resolve().parent.parent
    if args.cmd == "benchmark":
        import runpy
        sys.argv = [str(root / "scripts" / "benchmark.py"), *extra]
        runpy.run_path(sys.argv[0], run_name="__main__")
        return 0
    if args.cmd == "selftest":
        # A fresh interpreter, so the tests' temporary POCKETAI_HOME takes effect before any
        # backend import (this process already imported config with the real data folder).
        import subprocess
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", str(root / "tests"), *extra]
        return subprocess.call(cmd, cwd=str(root))

    if args.cmd == "serve":
        return cmd_serve(args)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "test":
        return cmd_test(args)
    if args.cmd == "version":
        print(__version__)
        return 0
    from terminal import run_terminal       # default: terminal assistant
    return run_terminal()


if __name__ == "__main__":
    sys.exit(main())
