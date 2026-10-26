"""Launcher and portability checks."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers

REPO = helpers.REPO
SH = ["start-linux.sh", "setup-linux.sh", "pocketai.sh", "scripts/linux-common.sh"]
BAT = ["start-windows.bat", "setup-windows.bat", "pocketai.bat"]
BASH = shutil.which("bash") or next((p for p in (r"C:\Program Files\Git\bin\bash.exe",) if Path(p).is_file()), None)


class LauncherTextTests(unittest.TestCase):
    def test_no_hardcoded_absolute_paths(self):
        for name in SH + BAT:
            lines = (REPO / name).read_text(encoding="utf-8").splitlines()
            text = "\n".join(l for l in lines if not re.match(r"\s*(#|rem\b|::)", l, re.I))
            with self.subTest(name=name):
                self.assertNotRegex(text, r"(?<![%\w])[A-Za-z]:\\(?!\\)", "hard-coded drive letter")
                self.assertNotRegex(text, r"/media/|/mnt/|/home/\w")

    def test_paths_resolved_from_script_location(self):
        for name in BAT:
            self.assertIn("%~dp0", (REPO / name).read_text())
        for name in ("start-linux.sh", "setup-linux.sh", "pocketai.sh"):
            self.assertIn('${BASH_SOURCE[0]}', (REPO / name).read_text())

    def test_bat_files_use_crlf_and_sh_files_use_lf(self):
        for name in BAT:
            raw = (REPO / name).read_bytes()
            self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"), f"{name} must use CRLF")
        for name in SH:
            self.assertNotIn(b"\r\n", (REPO / name).read_bytes(), f"{name} must use LF")

    def test_paths_are_quoted(self):
        # every use of the root/runtime variables must be inside quotes (paths with spaces)
        for name in BAT:
            for line in (REPO / name).read_text().splitlines():
                for m in re.finditer(r"%(ROOT|PY|PYDIR|ZIP)%", line):
                    before = line[:m.start()]
                    with self.subTest(name=name, line=line.strip()):
                        self.assertEqual(before.count('"') % 2, 1, "unquoted path variable")

    @unittest.skipUnless(BASH, "bash not available")
    def test_shell_syntax(self):
        for name in SH:
            with self.subTest(name=name):
                r = subprocess.run([BASH, "-n", str(REPO / name)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)


class PortablePathTests(unittest.TestCase):
    def test_app_resolves_paths_from_its_own_location(self):
        """Copy the app to a folder with spaces; it must use that folder, not the original."""
        dest = Path(tempfile.mkdtemp(prefix="Pocket AI copy ")) / "My USB" / "PocketAI-USB"
        shutil.copytree(REPO / "app", dest / "app", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "prompts", dest / "prompts")
        env = {k: v for k, v in os.environ.items() if k not in ("POCKETAI_HOME", "POCKETAI_RUNTIME_DIR")}
        env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if "site-packages" in p)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from backend import config; "
                "print(config.ROOT); print(config.MODELS_DIR); print(config.llama_server_path())")
        r = subprocess.run([sys.executable, "-c", code, str(dest / "app")], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        root, models, server = r.stdout.splitlines()
        self.assertEqual(Path(root), dest.resolve())
        self.assertEqual(Path(models), dest.resolve() / "models")
        self.assertTrue(server.startswith(str(dest.resolve() / "runtime")))

    def test_check_command_runs(self):
        env = dict(os.environ)
        r = subprocess.run([sys.executable, str(REPO / "app" / "pocketai.py"), "check"],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertIn("Python packages present", r.stdout)
        self.assertIn(r.returncode, (0, 1))


if __name__ == "__main__":
    unittest.main()
