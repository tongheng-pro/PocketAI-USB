"""PocketAI USB setup (the only part that needs the internet).

Run through setup-windows.bat or setup-linux.sh, which first fetch a portable Python.

    python scripts/setup.py                   interactive setup for this OS
    python scripts/setup.py --also-other-os   also prepare the runtime for the other OS
    python scripts/setup.py --model qwen3-1.7b  download that model (the flag counts as approval)
    python scripts/setup.py --report          only print the storage report

Every download is checked against a pinned SHA-256. Models are never downloaded without
the user's explicit approval. No existing files are deleted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from backend import config, database, model_manager  # noqa: E402

# ------------------------------------------------------------------------------- pins
LLAMA_TAG = "b11898"
LLAMA = {
    "windows": (f"llama-{LLAMA_TAG}-bin-win-cpu-x64.zip",
                "b6f5c7ebd16502016713b23758b4ce7255431fa287cef7022e842f66bf0153d0"),
    "linux":   (f"llama-{LLAMA_TAG}-bin-ubuntu-x64.tar.gz",
                "8d8d9d00d3b301537fc56e8052cdfeaeff52748edebd7fc758291dd3374c9eb8"),
}
LLAMA_URL = "https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{name}"
PYTHON = {
    "windows": ("https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip",
                "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"),
    "linux":   ("https://github.com/astral-sh/python-build-standalone/releases/download/20261021/"
                "cpython-3.12.15%2B20261021-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
                "6d7521138fd33afddc1ccd08abb007d9a2829e38117c2576f9efdc0ec73ce772"),
}
PIP_PYZ = "https://bootstrap.pypa.io/pip/pip.pyz"
PY_TAG = "3.12"
WHEEL_PLATFORMS = {
    "windows": ["win_amd64"],
    "linux": ["manylinux2014_x86_64", "manylinux_2_17_x86_64", "manylinux_2_28_x86_64"],
}
WIN_PTH = "python312.zip\n.\n..\\site-packages\n..\\..\\..\\app\n"

TOTAL_BUDGET = 7 * 1024**3        # keep the whole install below 7 GB on an 8 GB stick


# ------------------------------------------------------------------------------ helpers

def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    say(f"\n== {msg}")


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return str(n)


def ask(question: str, default: bool = False, assume_yes: bool = False) -> bool:
    if assume_yes:
        say(f"{question} [yes - --yes given]")
        return True
    if not sys.stdin.isatty():
        say(f"{question} [{'yes' if default else 'no'} - not interactive]")
        return default
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{question} {hint} ").strip().lower()
    except EOFError:
        return default
    return default if not answer else answer in ("y", "yes")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, expected_sha: str, size_hint: int = 0) -> Path:
    """Download with resume + progress; the file only gets its final name once verified."""
    if dest.exists() and sha256(dest) == expected_sha:
        say(f"  already downloaded: {dest.name}")
        return dest
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    start = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "PocketAI-USB-setup"})
    if start:
        req.add_header("Range", f"bytes={start}-")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416 and start:          # .part already complete
            resp = None
        else:
            raise
    if resp is not None:
        mode = "ab" if start and resp.status == 206 else "wb"
        if mode == "wb":
            start = 0
        total = int(resp.headers.get("Content-Length") or 0) + start or size_hint
        done, t0, last = start, time.time(), 0.0
        with resp, open(part, mode) as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last > 0.5:
                    speed = (done - start) / max(now - t0, 1e-3)
                    pct = f"{done * 100 / total:5.1f}%" if total else ""
                    print(f"\r  {dest.name}: {pct} {human(done)} / {human(total)}  {human(speed)}/s   ",
                          end="", flush=True)
                    last = now
        print()
    got = sha256(part)
    if got != expected_sha:
        part.unlink()       # our own incomplete download, not a user file
        raise SystemExit(f"  CHECKSUM MISMATCH for {dest.name}\n  expected {expected_sha}\n  got      {got}\n"
                         "  The download was discarded. Check your connection and run setup again.")
    os.replace(part, dest)
    say(f"  verified SHA-256: {dest.name}")
    return dest


def _deref_extract_tar(archive: Path, dest: Path, select, strip: int = 0) -> int:
    """Extract a tar, turning symlinks into real copies (FAT/exFAT/NTFS-from-Windows safe)."""
    count = 0
    with tarfile.open(archive) as tf:
        members = {m.name: m for m in tf.getmembers()}

        def resolve(m: tarfile.TarInfo, depth: int = 0) -> tarfile.TarInfo | None:
            while m is not None and (m.issym() or m.islnk()) and depth < 20:
                target = m.linkname if m.islnk() else os.path.normpath(
                    os.path.join(os.path.dirname(m.name), m.linkname)).replace("\\", "/")
                m = members.get(target)
                depth += 1
            return m

        for m in tf.getmembers():
            parts = m.name.split("/")[strip:]
            if not parts or not parts[0] or ".." in parts or m.name.startswith("/"):
                continue
            rel = "/".join(parts)
            if not select(rel, m):
                continue
            out = dest / rel
            if m.isdir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            real = resolve(m)
            if real is None or not real.isfile():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(real)
            if src is None:
                continue
            with src, open(out, "wb") as f:
                shutil.copyfileobj(src, f)
            mode = real.mode | (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH if real.mode & 0o111 else 0)
            try:
                os.chmod(out, mode & 0o777)
            except OSError:
                pass
            count += 1
    return count


def elf_needed(path: Path) -> list[str]:
    """DT_NEEDED library names of a 64-bit little-endian ELF file (stdlib only)."""
    import struct
    data = path.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return []
    phoff, = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    loads, dynamic = [], None
    for i in range(phnum):
        off = phoff + i * phentsize
        p_type, = struct.unpack_from("<I", data, off)
        p_offset, p_vaddr = struct.unpack_from("<QQ", data, off + 8)
        p_filesz, = struct.unpack_from("<Q", data, off + 32)
        if p_type == 1:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == 2:
            dynamic = (p_offset, p_filesz)
    if not dynamic:
        return []
    entries, strtab = [], None
    for off in range(dynamic[0], dynamic[0] + dynamic[1], 16):
        tag, val = struct.unpack_from("<qQ", data, off)
        if tag == 0:
            break
        if tag == 1:
            entries.append(val)
        elif tag == 5:
            strtab = val
    if strtab is None:
        return []
    base = next((strtab - v + o for v, o, s in loads if v <= strtab < v + s), None)
    if base is None:
        return []
    names = []
    for e in entries:
        end = data.index(b"\x00", base + e)
        names.append(data[base + e:end].decode())
    return names


# ------------------------------------------------------------------------------ steps

def runtime_dir(target: str) -> Path:
    return ROOT / "runtime" / target


def prepare_llama(target: str, tmp: Path, assume_yes: bool) -> None:
    llama_dir = runtime_dir(target) / "llama"
    marker = llama_dir / "VERSION"
    exe = "llama-server.exe" if target == "windows" else "llama-server"
    if (llama_dir / exe).exists() and marker.exists() and marker.read_text().strip() == LLAMA_TAG:
        say(f"  llama.cpp {LLAMA_TAG} already present for {target}")
        return
    if (llama_dir / exe).exists():
        old = marker.read_text().strip() if marker.exists() else "unknown"
        if not ask(f"  Replace llama.cpp {old} with {LLAMA_TAG} for {target}?", True, assume_yes):
            return
    name, digest = LLAMA[target]
    archive = download(LLAMA_URL.format(tag=LLAMA_TAG, name=name), tmp / name, digest)
    llama_dir.mkdir(parents=True, exist_ok=True)
    if target == "windows":
        with zipfile.ZipFile(archive) as zf:
            n = 0
            for info in zf.infolist():
                base = Path(info.filename).name
                keep = (base == "llama-server.exe" or base.upper().startswith("LICENSE") or
                        (base.endswith(".dll") and (not base.endswith("-impl.dll") or base == "llama-server-impl.dll")))
                if keep and not info.is_dir():
                    with zf.open(info) as src, open(llama_dir / base, "wb") as f:
                        shutil.copyfileobj(src, f)
                    n += 1
    else:
        def select(rel: str, m: tarfile.TarInfo) -> bool:
            base = rel.rsplit("/", 1)[-1]
            return base == "llama-server" or ".so" in base or base.upper().startswith("LICENSE")

        tmp_extract = tmp / "llama-linux"
        _deref_extract_tar(archive, tmp_extract, select)
        files = {f.name: f for f in tmp_extract.rglob("*") if f.is_file()}
        # Copy only what llama-server links against (DT_NEEDED closure) plus the CPU
        # backends ggml loads at runtime. The archive's .so symlink aliases would otherwise
        # become 3 full copies each on filesystems without symlinks.
        keep = {n for n in files if n.startswith("libggml-cpu-") or n.upper().startswith("LICENSE")}
        todo = ["llama-server"]
        while todo:
            name = todo.pop()
            if name in keep or name not in files:
                continue
            keep.add(name)
            todo.extend(elf_needed(files[name]))
        for name in sorted(keep):
            shutil.copy2(files[name], llama_dir / name)
        n = len(keep)
    marker.write_text(LLAMA_TAG)
    say(f"  installed {n} llama.cpp files into runtime/{target}/llama")


def prepare_python(target: str, tmp: Path) -> None:
    py_dir = runtime_dir(target) / "python"
    exe = py_dir / ("python.exe" if target == "windows" else "bin/python3.12")
    if exe.exists():
        say(f"  portable Python already present for {target}")
        return
    url, digest = PYTHON[target]
    archive = download(url, tmp / url.rsplit("/", 1)[-1].replace("%2B", "+"), digest)
    py_dir.mkdir(parents=True, exist_ok=True)
    if target == "windows":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(py_dir)
        (py_dir / "python312._pth").write_text(WIN_PTH)
    else:
        # Symlinks here are only aliases (bin/python3 -> python3.12, libpython3.12.so ->
        # .so.1.0); the launchers call bin/python3.12 directly, so skip them instead of
        # writing 30 MB duplicates.
        # python3.12 is statically linked, so the 32 MB libpython shared library is unused.
        n = _deref_extract_tar(archive, py_dir, lambda rel, m: not m.issym() and
                               not rel.startswith("lib/libpython"), strip=1)
        say(f"  extracted {n} files")
    say(f"  portable Python ready in runtime/{target}/python")


def _pip_command(tmp: Path) -> list[str]:
    try:
        import pip  # noqa: F401
        return [sys.executable, "-m", "pip"]
    except ImportError:
        pyz = tmp / "pip.pyz"
        if not pyz.exists():
            say("  fetching pip (zipapp, not installed anywhere)")
            req = urllib.request.Request(PIP_PYZ, headers={"User-Agent": "PocketAI-USB-setup"})
            with urllib.request.urlopen(req, timeout=60) as r, open(pyz, "wb") as f:
                shutil.copyfileobj(r, f)
        return [sys.executable, str(pyz)]


def install_packages(target: str, tmp: Path) -> None:
    site = runtime_dir(target) / "site-packages"
    req = ROOT / "requirements.txt"
    req_hash = hashlib.sha256(req.read_bytes()).hexdigest()
    marker = site / ".requirements.sha256"
    if marker.exists() and marker.read_text().strip() == req_hash:
        say(f"  Python packages up to date for {target}")
        return
    site.mkdir(parents=True, exist_ok=True)
    cmd = _pip_command(tmp) + [
        "install", "--target", str(site), "--upgrade", "--only-binary=:all:",
        "--python-version", PY_TAG, "--implementation", "cp", "--abi", "cp312",
        "--disable-pip-version-check", "--no-warn-script-location", "--no-cache-dir",
        "-r", str(req)]
    for plat in WHEEL_PLATFORMS[target]:
        cmd += ["--platform", plat]
    say(f"  installing {', '.join(l.split('==')[0] for l in req.read_text().splitlines() if '==' in l)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        say(result.stdout[-3000:])
        say(result.stderr[-3000:])
        raise SystemExit("  pip failed - see the output above")
    marker.write_text(req_hash)
    say(f"  packages installed into runtime/{target}/site-packages")


def init_data() -> None:
    config.ensure_dirs()
    database.init_db()
    if not config.SETTINGS_FILE.exists():
        config.save_settings({})
        say("  created config/settings.json with defaults")
    else:
        say("  kept existing config/settings.json")
    say(f"  database ready: {config.DB_FILE.relative_to(ROOT)}")


def _ram_total() -> int:
    try:
        import psutil
        return psutil.virtual_memory().total
    except ImportError:
        if hasattr(os, "sysconf"):
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return 0


def choose_models(requested: list[str], interactive: bool) -> None:
    found = [m for m in model_manager.discover() if m["valid"]]
    for m in found:
        say(f"  found {m['file']} ({human(m['size_bytes'])})")
    catalog = {c["id"]: c for c in model_manager.CATALOG}
    wanted: list[dict] = []
    for mid in requested:
        if mid not in catalog:
            raise SystemExit(f"  unknown model id '{mid}'. Choose from: {', '.join(catalog)}")
        wanted.append(catalog[mid])

    if not requested and interactive and sys.stdin.isatty():
        ram = _ram_total()
        rec = "qwen3-1.7b" if ram >= 7.5 * 1024**3 else "qwen3-0.6b"
        say("\n  Optional model downloads (from Hugging Face):")
        for i, c in enumerate(model_manager.CATALOG, 1):
            tag = "  <- recommended for this PC" if c["id"] == rec else ""
            present = " [already installed]" if (config.MODELS_DIR / c["file"]).exists() else ""
            say(f"   {i}. {c['name']:<34} {human(c['size']):>9}  license: {c['license']}, "
                f"needs ~{c['min_ram_gb']} GB RAM{present}{tag}")
        say(f"  This computer has {human(ram)} RAM.")
        try:
            pick = input("  Download which models? Enter numbers like 1 or 1,2 (Enter = none): ").strip()
        except EOFError:
            pick = ""
        for token in pick.replace(" ", "").split(","):
            if token.isdigit() and 1 <= int(token) <= len(model_manager.CATALOG):
                wanted.append(model_manager.CATALOG[int(token) - 1])
        if wanted:
            names = ", ".join(f"{c['name']} ({human(c['size'])}, {c['license']})" for c in wanted)
            if not ask(f"  Download {names}? By downloading you accept each model's license.", False):
                wanted = []

    for c in wanted:
        dest = config.MODELS_DIR / c["file"]
        if dest.exists() and dest.stat().st_size == c["size"]:
            say(f"  {c['file']} already present")
            continue
        free = shutil.disk_usage(config.MODELS_DIR).free
        if free < c["size"] + 200 * 1024**2:
            say(f"  not enough free space for {c['file']} ({human(c['size'])} needed, {human(free)} free)")
            continue
        url = f"https://huggingface.co/{c['repo']}/resolve/main/{c['file']}"
        say(f"  downloading {c['name']} - {human(c['size'])}")
        download(url, dest, c["sha256"], c["size"])
        if free - c["size"] < 1024**3:
            say("  note: less than 1 GB will be left free on the drive")

    settings = config.load_settings()
    found = [m for m in model_manager.discover() if m["valid"]]
    if found and not any(m["id"] == settings["active_model"] for m in found):
        pref = [m for m in found if "1.7b" in m["file"].lower()] or found
        config.save_settings({"active_model": pref[0]["id"]})
        say(f"  active model set to {pref[0]['id']}")
    if not found:
        say(f"  No model installed. Copy any chat .gguf file into {config.MODELS_DIR} "
            "or run setup again to download one.")


def run_test(target: str) -> bool:
    if target != config.os_name():
        return True
    if not [m for m in model_manager.discover() if m["valid"]]:
        say("  skipped (no model installed)")
        return True
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(runtime_dir(target) / "site-packages"), str(ROOT / "app")])
    r = subprocess.run([sys.executable, str(ROOT / "app" / "pocketai.py"), "test"], env=env)
    return r.returncode == 0


def storage_report() -> int:
    def size(p: Path) -> int:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    rows = []
    for p in sorted(ROOT.iterdir()):
        if p.name.startswith(".") and p.name != ".gitignore":
            continue
        rows.append((p.name + ("/" if p.is_dir() else ""), size(p)))
    total = sum(s for _, s in rows)
    for name, s in sorted(rows, key=lambda r: -r[1]):
        if s > 0:
            say(f"  {name:<28} {human(s):>10}")
    for target in ("windows", "linux"):
        d = runtime_dir(target)
        if d.exists():
            parts = ", ".join(f"{c.name} {human(size(c))}" for c in sorted(d.iterdir()) if c.is_dir())
            say(f"    runtime/{target}: {parts}")
    du = shutil.disk_usage(ROOT)
    say(f"  {'TOTAL':<28} {human(total):>10}   (budget {human(TOTAL_BUDGET)})")
    say(f"  drive: {human(du.free)} free of {human(du.total)}")
    if total > TOTAL_BUDGET:
        say("  WARNING: installation is larger than the 7 GB budget for an 8 GB drive")
    return total


# ------------------------------------------------------------------------------- main

def main() -> int:
    p = argparse.ArgumentParser(description="PocketAI USB setup")
    p.add_argument("--target", choices=["windows", "linux"], default=config.os_name())
    p.add_argument("--also-other-os", action="store_true", help="also prepare the other OS's runtime")
    p.add_argument("--model", action="append", default=[], help="catalog id to download (implies approval)")
    p.add_argument("--no-models", action="store_true", help="skip the model step")
    p.add_argument("--skip-test", action="store_true")
    p.add_argument("--yes", action="store_true", help="answer yes to runtime questions (never to model downloads)")
    p.add_argument("--report", action="store_true", help="only print the storage report")
    args = p.parse_args()

    if args.report:
        storage_report()
        return 0

    step("1/7 System check")
    arch = config.machine_arch()
    say(f"  host: {platform.system()} {platform.release()} / {arch} / Python {platform.python_version()}")
    say(f"  application folder: {ROOT}")
    if arch != "x86_64":
        raise SystemExit("  PocketAI USB supports x86_64 (64-bit Intel/AMD) computers only.")
    if args.target not in ("windows", "linux"):
        raise SystemExit("  unsupported operating system")
    targets = [args.target]
    if args.also_other_os:
        targets.append("linux" if args.target == "windows" else "windows")

    with tempfile.TemporaryDirectory(prefix="pocketai-setup-") as t:
        tmp = Path(t)
        for target in targets:
            step(f"2/7 Portable Python ({target})")
            prepare_python(target, tmp)
            step(f"3/7 llama.cpp inference engine ({target})")
            prepare_llama(target, tmp, args.yes)
            step(f"4/7 Python packages ({target})")
            install_packages(target, tmp)

    step("5/7 Database and configuration")
    init_data()

    step("6/7 AI models")
    if args.no_models:
        say("  skipped (--no-models)")
    else:
        choose_models(args.model, interactive=True)

    step("7/7 Inference test")
    ok = True
    if args.skip_test:
        say("  skipped (--skip-test)")
    else:
        ok = run_test(args.target)

    step("Storage report")
    storage_report()
    say("")
    if ok:
        start = "start-windows.bat" if args.target == "windows" else "bash start-linux.sh"
        say(f"Setup finished. PocketAI now works offline. Start it with:  {start}")
        return 0
    say("Setup finished, but the inference test failed. See docs/TROUBLESHOOTING.md and logs/llama-server.log.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
