"""Measure model load time and generation speed on this computer.

    Windows:  pocketai.bat benchmark [--model FILE] [--ctx 2048]
    Ubuntu:   bash pocketai.sh benchmark [--model FILE] [--ctx 2048]

Results are printed and appended to logs/benchmark.csv. Close the dashboard first so the
benchmark has the CPU to itself.
"""
from __future__ import annotations

import argparse
import csv
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import psutil  # noqa: E402

from backend import config, model_manager  # noqa: E402
from backend.ai_engine import engine  # noqa: E402
from backend.system_monitor import cpu_name  # noqa: E402

PROMPTS = [
    ("short", "In one sentence, what is a USB flash drive?", 64),
    ("code", "Write a Python function that returns the n-th Fibonacci number iteratively.", 256),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", action="append", help="model file in models/ (default: all)")
    p.add_argument("--ctx", type=int, default=2048)
    p.add_argument("--threads", type=int, default=0)
    args = p.parse_args()

    models = [m for m in model_manager.discover() if m["valid"]]
    if args.model:
        models = [m for m in models if m["id"] in args.model]
    if not models:
        print("No models to benchmark.")
        return 1
    settings = dict(config.load_settings(), ctx_size=args.ctx, threads=args.threads, low_memory=False)
    out = config.LOGS_DIR / "benchmark.csv"
    new = not out.exists()
    print(f"CPU: {cpu_name()} ({psutil.cpu_count(logical=False)} cores), "
          f"RAM free: {psutil.virtual_memory().available / 2**30:.1f} GB")
    with open(out, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "os", "cpu", "model", "ctx", "threads", "load_s", "prompt",
                        "prompt_tok_s", "gen_tok_s", "tokens", "first_token_s"])
        for m in models:
            t0 = time.time()
            try:
                engine.load(m["id"], settings, force=True, wait=True)
            except Exception as exc:
                print(f"{m['id']}: load failed: {exc}")
                continue
            load_s = time.time() - t0
            print(f"\n{m['display_name']}  (loaded in {load_s:.1f}s)")
            for name, prompt, max_tokens in PROMPTS:
                params = dict(config.effective(settings), max_tokens=max_tokens, temperature=0.2,
                              enable_thinking=False)
                stats = {}
                for ev in engine.stream_chat([{"role": "user", "content": prompt}], params):
                    if ev["type"] == "done":
                        stats = ev["stats"]
                print(f"  {name:<6} prompt {stats.get('prompt_tokens_per_s')} tok/s | "
                      f"generation {stats.get('tokens_per_s')} tok/s | {stats.get('completion_tokens')} tokens | "
                      f"first token {stats.get('time_to_first_token_s')}s")
                w.writerow([time.strftime("%Y-%m-%d %H:%M"), platform.platform(), cpu_name(), m["id"], args.ctx,
                            engine.load_settings.get("threads"), round(load_s, 1), name,
                            stats.get("prompt_tokens_per_s"), stats.get("tokens_per_s"),
                            stats.get("completion_tokens"), stats.get("time_to_first_token_s")])
            engine.unload()
    print(f"\nSaved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
