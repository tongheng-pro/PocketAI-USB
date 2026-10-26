"""llama.cpp inference engine.

Runs the official ``llama-server`` binary as a child process bound to 127.0.0.1 with a random
API key, and streams completions from its OpenAI-compatible endpoint. Only one model is
loaded at a time; switching models restarts the server.

The child is tied to this process (a Windows Job Object / Linux PR_SET_PDEATHSIG) so it is
killed even if the launcher window is closed or the backend crashes.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from . import config, model_manager

log = logging.getLogger("pocketai.engine")

LOAD_TIMEOUT_S = 300           # slow USB 2.0 ports can take minutes to read a 1 GB model


class EngineError(RuntimeError):
    pass


class MemoryWarning(EngineError):
    """Raised when a model probably does not fit in available RAM (load with force=True)."""

    def __init__(self, message: str, needed: int, available: int):
        super().__init__(message)
        self.needed = needed
        self.available = available


class BusyError(EngineError):
    pass


# ------------------------------------------------------------------ child-process binding

def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_job_handle = None


def _bind_to_parent(proc: subprocess.Popen) -> None:
    """Windows: put the child in a job that is killed when this process exits."""
    global _job_handle
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        if _job_handle is None:
            job = k32.CreateJobObjectW(None, None)

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class IOC(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in
                            ("r", "w", "o", "rt", "wt", "ot")]

            class EXT(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IOC),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            info = EXT()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise OSError(ctypes.get_last_error())
            _job_handle = job
        handle = k32.OpenProcess(0x0001 | 0x0100, False, proc.pid)  # TERMINATE | SET_QUOTA
        if not handle or not k32.AssignProcessToJobObject(_job_handle, handle):
            raise OSError(ctypes.get_last_error())
        k32.CloseHandle(handle)
    except Exception as exc:  # the explicit shutdown path still works without the job
        log.warning("could not bind llama-server to a job object: %s", exc)


def _linux_preexec() -> None:
    import ctypes
    import signal
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


# ------------------------------------------------------------------------------ engine

class LlamaEngine:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._gen_lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.state = "stopped"          # stopped | loading | ready | error
        self.model_id = ""
        self.error = ""
        self.port = 0
        self.api_key = ""
        self.loaded_at = 0.0
        self.load_settings: dict[str, Any] = {}
        self._active_conn: http.client.HTTPConnection | None = None
        self._cancel = threading.Event()
        self._load_seq = 0

    # ---------------------------------------------------------------- status
    @property
    def generating(self) -> bool:
        return self._gen_lock.locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self.proc is not None and self.proc.poll() is not None and self.state != "stopped":
                code = self.proc.returncode
                self.state = "error"
                self.error = self.error or f"llama-server exited unexpectedly (code {code}); see logs/llama-server.log"
                self.proc = None
            return {
                "state": self.state, "model": self.model_id, "error": self.error,
                "generating": self.generating, "loaded_at": self.loaded_at,
                "pid": self.proc.pid if self.proc else None,
                "settings": self.load_settings,
                "binary_present": config.llama_server_path().is_file(),
            }

    # ---------------------------------------------------------------- memory check
    @staticmethod
    def check_memory(info: dict[str, Any], ctx_size: int) -> tuple[int, int]:
        import psutil
        need = model_manager.estimate_memory(info, ctx_size)
        avail = psutil.virtual_memory().available
        return need, avail

    # ---------------------------------------------------------------- load / unload
    def load(self, mid: str, settings: dict[str, Any], force: bool = False, wait: bool = False) -> None:
        """Start llama-server for ``mid``. Raises MemoryWarning unless ``force``.

        Loading continues in a background thread unless ``wait`` is set.
        """
        binary = config.llama_server_path()
        if not binary.is_file():
            raise EngineError(f"llama-server not found at {binary}. Run the setup script for this OS.")
        path = model_manager.resolve_model(mid)
        info = model_manager.describe(path)
        if not info["valid"]:
            raise EngineError(f"{mid}: {info['error']}")
        eff = config.effective(settings)
        ctx = eff["ctx_size"]
        if info.get("context_train") and ctx > info["context_train"]:
            ctx = info["context_train"]
        need, avail = self.check_memory(info, ctx)
        if need > avail and not force:
            raise MemoryWarning(
                f"{info['display_name']} needs about {need / 2**30:.1f} GB RAM with a {ctx}-token "
                f"context, but only {avail / 2**30:.1f} GB is available. Close other programs, "
                f"pick a smaller model, lower the context length, or load anyway.", need, avail)
        if self.generating:
            raise BusyError("a reply is being generated; stop it before switching models")

        with self._lock:
            self._stop_locked()
            threads = eff["threads"] or model_manager.default_threads()
            self.port = free_port()
            self.api_key = secrets.token_urlsafe(24)
            args = [str(binary), "-m", str(path), "--host", "127.0.0.1", "--port", str(self.port),
                    "--api-key", self.api_key, "-c", str(ctx), "-t", str(threads), "-np", "1",
                    "--no-webui", "--reasoning-format", "deepseek", "--log-colors", "off"]
            if eff["low_memory"]:
                args += ["-b", "256", "-ub", "128", "-cram", "0"]
            else:
                args += ["-cram", "512"]
            config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
            logf = open(config.LOGS_DIR / "llama-server.log", "w", encoding="utf-8", errors="replace")
            env = dict(os.environ)
            kwargs: dict[str, Any] = {}
            if os.name == "nt":
                kwargs["creationflags"] = 0x08000000      # CREATE_NO_WINDOW
            else:
                libdir = str(binary.parent)
                env["LD_LIBRARY_PATH"] = libdir + os.pathsep + env.get("LD_LIBRARY_PATH", "")
                kwargs["preexec_fn"] = _linux_preexec
            log.info("starting llama-server: model=%s ctx=%s threads=%s", mid, ctx, threads)
            try:
                self.proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT,
                                             stdin=subprocess.DEVNULL, cwd=str(binary.parent),
                                             env=env, **kwargs)
            except OSError as exc:
                logf.close()
                self.state, self.error = "error", f"could not start llama-server: {exc}"
                raise EngineError(self.error) from exc
            logf.close()   # the child keeps its own handle
            _bind_to_parent(self.proc)
            self.state, self.model_id, self.error = "loading", mid, ""
            self.load_settings = {"ctx_size": ctx, "threads": threads, "low_memory": eff["low_memory"]}
            self._load_seq += 1
            seq = self._load_seq

        t = threading.Thread(target=self._wait_ready, args=(seq,), daemon=True)
        t.start()
        if wait:
            t.join()
            if self.state != "ready":
                raise EngineError(self.error or "model failed to load")

    def _wait_ready(self, seq: int) -> None:
        deadline = time.time() + LOAD_TIMEOUT_S
        while time.time() < deadline:
            with self._lock:
                if seq != self._load_seq or self.proc is None:
                    return
                if self.proc.poll() is not None:
                    self.state = "error"
                    self.error = (f"llama-server exited during load (code {self.proc.returncode}). "
                                  f"{_log_tail()}")
                    self.proc = None
                    return
            try:
                code, _ = self._request("GET", "/health", timeout=2)
                if code == 200:
                    with self._lock:
                        if seq == self._load_seq:
                            self.state, self.loaded_at = "ready", time.time()
                            log.info("model ready: %s", self.model_id)
                    return
            except OSError:
                pass
            time.sleep(0.5)
        with self._lock:
            if seq == self._load_seq:
                self._stop_locked()
                self.state, self.error = "error", "timed out waiting for the model to load"

    def unload(self) -> None:
        self.cancel()
        with self._lock:
            self._load_seq += 1
            self._stop_locked()
            self.state, self.model_id, self.error = "stopped", "", ""

    def _stop_locked(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    # ---------------------------------------------------------------- HTTP helpers
    def _request(self, method: str, path: str, body: dict | None = None,
                 timeout: float = 30) -> tuple[int, Any]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            conn.request(method, path, body=json.dumps(body) if body is not None else None,
                         headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            try:
                parsed = json.loads(data) if data else None
            except json.JSONDecodeError:
                parsed = data.decode("utf-8", "replace")
            return resp.status, parsed
        finally:
            conn.close()

    # ---------------------------------------------------------------- generation
    def cancel(self) -> None:
        """Stop the running generation (closing the socket makes llama-server abort it)."""
        self._cancel.set()
        conn = self._active_conn
        if conn is not None and conn.sock is not None:
            try:
                conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def stream_chat(self, messages: list[dict[str, str]], params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Yield ``{"type": "content"|"reasoning", "text": ...}`` then ``{"type": "done", ...}``."""
        if self.status()["state"] != "ready":
            raise EngineError("no model is loaded")
        if not self._gen_lock.acquire(blocking=False):
            raise BusyError("another reply is already being generated")
        self._cancel.clear()
        started = time.time()
        first_token_at = 0.0
        body = {
            "messages": messages, "stream": True,
            "temperature": params["temperature"], "top_p": params["top_p"],
            "max_tokens": params["max_tokens"],
            "chat_template_kwargs": {"enable_thinking": bool(params.get("enable_thinking"))},
            "timings_per_token": False,
        }
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=600)
        self._active_conn = conn
        finish_reason, timings, n_chunks = "", {}, 0
        try:
            conn.request("POST", "/v1/chat/completions", body=json.dumps(body), headers={
                "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                "Accept": "text/event-stream"})
            resp = conn.getresponse()
            if resp.status != 200:
                detail = resp.read().decode("utf-8", "replace")[:500]
                raise EngineError(f"llama-server returned {resp.status}: {detail}")
            while not self._cancel.is_set():
                line = resp.readline()
                if not line:
                    break
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if "error" in chunk:
                    raise EngineError(str(chunk["error"].get("message", chunk["error"])))
                if chunk.get("timings"):
                    timings = chunk["timings"]
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    for kind, key in (("reasoning", "reasoning_content"), ("content", "content")):
                        text = delta.get(key)
                        if text:
                            if not first_token_at:
                                first_token_at = time.time()
                            n_chunks += 1
                            yield {"type": kind, "text": text}
        except (OSError, http.client.HTTPException) as exc:
            if not self._cancel.is_set():
                raise EngineError(f"connection to llama-server failed: {exc}") from exc
        finally:
            self._active_conn = None
            conn.close()
            self._gen_lock.release()
        stopped = self._cancel.is_set()
        stats = {
            "model": self.model_id, "finish_reason": "stopped" if stopped else finish_reason,
            "elapsed_s": round(time.time() - started, 2),
            "time_to_first_token_s": round(first_token_at - started, 2) if first_token_at else None,
        }
        if timings:
            stats.update({
                "prompt_tokens": timings.get("prompt_n"),
                "completion_tokens": timings.get("predicted_n"),
                "tokens_per_s": round(timings.get("predicted_per_second") or 0, 2),
                "prompt_tokens_per_s": round(timings.get("prompt_per_second") or 0, 2),
            })
        elif n_chunks:
            stats["completion_tokens"] = n_chunks
        yield {"type": "done", "stopped": stopped, "stats": stats}

    def process_rss(self) -> int:
        try:
            import psutil
            return psutil.Process(self.proc.pid).memory_info().rss if self.proc else 0
        except Exception:
            return 0


def _log_tail(lines: int = 6) -> str:
    try:
        text = (config.LOGS_DIR / "llama-server.log").read_text(encoding="utf-8", errors="replace")
        tail = [l for l in text.splitlines() if l.strip()][-lines:]
        return "Last log lines: " + " | ".join(tail)
    except OSError:
        return ""


engine = LlamaEngine()
