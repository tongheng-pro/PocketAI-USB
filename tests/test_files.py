"""File analyzer: validation, sensitive-file detection, path traversal, Laravel detection."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from backend import file_analyzer as fa

MAX = 256 * 1024


class InspectTests(unittest.TestCase):
    def test_plain_code_file(self):
        info = fa.inspect("app/Http/Kernel.php", b"<?php\nclass Kernel {}\n", MAX)
        self.assertTrue(info["allowed"])
        self.assertEqual(info["name"], "Kernel.php")
        self.assertEqual(info["language"], "php")
        self.assertEqual(info["lines"], 2)
        self.assertEqual(info["sensitive"], [])

    def test_binary_and_size_and_type(self):
        self.assertFalse(fa.inspect("a.txt", b"abc\x00def", MAX)["allowed"])
        self.assertTrue(fa.inspect("a.txt", b"abc\x00def", MAX)["binary"])
        self.assertFalse(fa.inspect("big.js", b"a" * (MAX + 1), MAX)["allowed"])
        self.assertFalse(fa.inspect("photo.png", b"hello", MAX)["allowed"])
        self.assertFalse(fa.inspect("prog.exe", b"MZ\x90\x00", MAX)["allowed"])
        self.assertTrue(fa.is_binary(bytes(range(128, 256)) * 10))

    def test_sensitive_names(self):
        for name in [".env", ".env.production", "id_rsa", "server.key", "cert.pem", "credentials.json",
                     "auth.json", ".npmrc", "wp-config.php"]:
            with self.subTest(name=name):
                self.assertTrue(fa.inspect(name, b"x=1\n", MAX)["sensitive"], name)

    def test_sensitive_content(self):
        samples = [
            b"-----BEGIN RSA PRIVATE KEY-----\nabc\n",
            b"DB_PASSWORD=hunter2\n",
            b"$config = ['password' => 'supersecret1'];\n",
            b"key = 'AKIAABCDEFGHIJKLMNOP'\n",
            b"token: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n",
        ]
        for s in samples:
            with self.subTest(s=s):
                self.assertTrue(fa.inspect("config.php", s, MAX)["sensitive"])
        self.assertEqual(fa.inspect("x.php", b"$password = $request->input('password');", MAX)["sensitive"], [])

    def test_build_prompt_truncates_and_handles_fences(self):
        msg, truncated = fa.build_prompt("a.md", "```js\nx\n```\n" + "y" * 5000, "markdown", "Explain.", 1000)
        self.assertTrue(truncated)
        self.assertIn("````markdown", msg)
        self.assertIn("first 1000 characters", msg)
        msg, truncated = fa.build_prompt("a.py", "print(1)", "python", "Explain.", 1000, guidance="G")
        self.assertFalse(truncated)
        self.assertTrue(msg.startswith("[File: a.py] Explain."))
        self.assertIn("Project context: G", msg)


class PathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pocketai proj "))
        self.root = self.tmp / "project"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "a.py").write_text("print('hi')\n")
        (self.root / ".env").write_text("APP_KEY=base64:xyz\n")
        (self.tmp / "outside.txt").write_text("secret outside\n")

    def test_traversal_blocked(self):
        for bad in ["../outside.txt", "src/../../outside.txt", "..\\outside.txt", "/etc/passwd",
                    "C:/Windows/win.ini", "c:\\x", "src/\x00a.py"]:
            with self.subTest(bad=bad), self.assertRaises(fa.FileAccessError):
                fa.read_file(self.root, bad, MAX)

    def test_reads_inside_root(self):
        name, data = fa.read_file(self.root, "src/a.py", MAX)
        self.assertEqual(name, "a.py")
        self.assertIn(b"print", data)
        self.assertEqual(fa.read_file(self.root, "/src/a.py", MAX)[0], "a.py")  # leading slash = root-relative

    def test_symlink_escape_blocked(self):
        link = self.root / "escape.txt"
        try:
            os.symlink(self.tmp / "outside.txt", link)
        except (OSError, NotImplementedError):
            self.skipTest("creating symlinks is not permitted on this system")
        with self.assertRaises(fa.FileAccessError):
            fa.read_file(self.root, "escape.txt", MAX)
        self.assertNotIn("escape.txt", [e["name"] for e in fa.list_dir(self.root)["entries"]])

    def test_list_dir(self):
        listing = fa.list_dir(self.root)
        names = {e["name"]: e for e in listing["entries"]}
        self.assertTrue(names["src"]["dir"])
        self.assertTrue(names[".env"]["sensitive"])
        self.assertEqual(fa.list_dir(self.root, "src")["path"], "src")
        with self.assertRaises(fa.FileAccessError):
            fa.list_dir(self.root, "..")

    def test_large_file_is_reported_too_large(self):
        (self.root / "big.txt").write_bytes(b"a" * 5000)
        name, data = fa.read_file(self.root, "big.txt", 1000)
        self.assertFalse(fa.inspect(name, data, 1000)["allowed"])

    def test_validate_root(self):
        self.assertEqual(fa.validate_root(str(self.root)), self.root.resolve())
        with self.assertRaises(fa.FileAccessError):
            fa.validate_root(str(self.tmp / "missing"))
        with self.assertRaises(fa.FileAccessError):
            fa.validate_root(Path(self.root.anchor).as_posix())
        with self.assertRaises(fa.FileAccessError):
            fa.validate_root(str(Path.home()))


class LaravelDetectionTests(unittest.TestCase):
    def make(self, require, lock_version=None, vendor_version=None):
        root = Path(tempfile.mkdtemp())
        (root / "composer.json").write_text(json.dumps({"require": require}))
        if lock_version:
            (root / "composer.lock").write_text(json.dumps(
                {"packages": [{"name": "laravel/framework", "version": lock_version}]}))
        if vendor_version:
            p = root / "vendor/laravel/framework/src/Illuminate/Foundation/Application.php"
            p.parent.mkdir(parents=True)
            p.write_text(f"<?php\nclass Application {{\n    const VERSION = '{vendor_version}';\n}}\n")
        return root

    def test_installed_version_preferred(self):
        root = self.make({"php": ">=5.5.9", "laravel/framework": "5.2.*"}, "v5.2.45", "5.2.45")
        p = fa.detect_project(root)
        self.assertEqual((p["framework"], p["version"], p["source"]), ("laravel", "5.2.45", "vendor/laravel/framework"))
        g = fa.project_guidance(p)
        self.assertIn("Laravel 5.2.45", g)
        self.assertIn("PHP >=5.5.9", g)
        self.assertIn("Laravel 5.x notes", g)

    def test_lock_fallback_and_plain_php(self):
        p = fa.detect_project(self.make({"laravel/framework": "^10.0"}, "v10.48.2"))
        self.assertEqual((p["version"], p["source"]), ("10.48.2", "composer.lock"))
        self.assertNotIn("5.x", fa.project_guidance(p))
        p = fa.detect_project(self.make({"php": "^8.1", "guzzlehttp/guzzle": "^7"}))
        self.assertEqual(p["framework"], "php")
        self.assertIn("PHP ^8.1", fa.project_guidance(p))
        self.assertIsNone(fa.detect_project(Path(tempfile.mkdtemp()))["framework"])


if __name__ == "__main__":
    unittest.main()
