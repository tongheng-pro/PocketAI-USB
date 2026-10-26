# Performance

## Storage usage (measured)

Measured on the reference drive after `setup-windows.bat --also-other-os` plus Qwen3 1.7B:

| Item | Size |
|---|---|
| `runtime/windows/python` (embeddable 3.12.10) | 21.5 MB |
| `runtime/windows/site-packages` | 17.1 MB |
| `runtime/windows/llama` (llama-server + CPU backends) | 43.5 MB |
| `runtime/linux/python` (standalone 3.12.15, stripped, no duplicate aliases) | 62.6 MB |
| `runtime/linux/site-packages` | 16.5 MB |
| `runtime/linux/llama` | 41.1 MB |
| **Runtimes, both OSes** | **~202 MB** |
| `app/`, `prompts/`, `docs/`, `scripts/`, `tests/` | < 1 MB |
| Qwen3 1.7B Q4_K_M | 1.03 GiB |
| **Total with one model** | **~1.25 GB** |

That leaves about 6 GB free on an 8 GB drive (7.5 GB usable). Adding all four catalog models brings the total to about 3.4 GB, still well under the 7 GB budget and with the 1 GB reserve. `python scripts/setup.py --report` (or the System Monitor's **Measure** button) shows the current numbers.

Space savings already applied: only `llama-server` and the libraries it actually links against are kept (setup reads the ELF `DT_NEEDED` entries); symlink aliases are not duplicated on filesystems without symlinks; the unused 32 MB `libpython` shared library is skipped; uvicorn is installed without its optional native extras.

## Memory requirements

RAM needed is roughly *model file* + *KV cache* + *about 400 MB of buffers and prompt cache*. The KV cache grows with the context length. The dashboard shows this estimate for every model and warns before loading one that does not fit.

PocketAI's estimates (f16 KV cache, from each model's GGUF metadata):

| Model | File | ctx 2048 | ctx 4096 | ctx 8192 |
|---|---|---|---|---|
| Qwen3 0.6B | 0.4 GB | ~1.1 GB | ~1.3 GB | ~1.9 GB |
| Gemma 3 1B | 0.8 GB | ~1.2 GB | ~1.4 GB | ~1.6 GB |
| Llama 3.2 1B | 0.8 GB | ~1.3 GB | ~1.4 GB | ~1.7 GB |
| Qwen3 1.7B | 1.1 GB | ~1.7 GB | ~2.0 GB | ~2.5 GB |

Measured: llama-server used **2.3 GB RSS** with Qwen3 1.7B at ctx 4096.

Add about 120 MB for the Python backend and the browser tab's own usage. Rules of thumb:

- **8 GB RAM**: Qwen3 1.7B at 4096 context, comfortable.
- **4 GB RAM**: Qwen3 0.6B (or Llama 3.2 1B) with **low-memory mode** on, and other apps closed.

## Benchmark (measured)

Reference PC: Intel Core i5-10300H (4 cores / 8 threads), 16 GB RAM, Windows 10, model on a USB drive, Qwen3 1.7B Q4_K_M, ctx 2048, 4 threads, other heavy programs running:

| Metric | Result |
|---|---|
| Model load from USB | 2.8 to 4.3 s |
| Prompt processing | ~35 to 39 tokens/s |
| Generation | ~8.5 to 12 tokens/s |
| Time to first token (short prompt) | ~0.6 s |

These are measurements on one machine, not a promise. Speed depends on the CPU (cores, AVX2/AVX-512), memory bandwidth, the model, the context length and what else is running.

### Run your own benchmark

Close the dashboard first, then:

```bash
pocketai.bat benchmark                        # Windows
bash pocketai.sh benchmark                    # Ubuntu
bash pocketai.sh benchmark --model Qwen3-0.6B-Q4_K_M.gguf --ctx 4096
```

Each run is appended to `logs/benchmark.csv` (date, OS, CPU, model, ctx, threads, load time, prompt and generation tokens/s, first-token time).

## Optimization tips

- **Slow replies**: use a smaller model (Qwen3 0.6B is about 2 to 3 times faster), lower the context length, keep *Show model thinking* off, and close other heavy programs.
- **Threads**: *automatic* uses the physical cores, which is usually fastest. Using all logical threads (hyper-threading) is often *slower*.
- **USB 2 ports** only slow down **loading** (the model is read once into RAM and cached by the OS). Generation speed is the same.
- **Long chats** get slower because the prompt grows. Start a new chat for a new topic, or lower "Messages remembered per chat".
- **Low-memory mode** caps context (2048), history (8 messages) and reply length (512), and uses smaller batches with no prompt cache.
- Disk writes are minimal: one SQLite write per message, a small rotating log, and no per-token writes.
