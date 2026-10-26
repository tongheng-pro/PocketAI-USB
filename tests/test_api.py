"""Backend endpoint tests against the real FastAPI app over HTTP (with a fake engine)."""
import base64
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import helpers
from backend import ai_engine, config


class FakeEngine:
    """Stands in for llama-server: streams fixed tokens, honours cancel()."""

    def __init__(self, tokens, delay=0.0):
        self.tokens, self.delay = tokens, delay
        self.cancelled = threading.Event()
        self.seen_messages = None

    def install(self, test):
        eng = ai_engine.engine
        orig = (eng.status, eng.stream_chat, eng.cancel)
        lock = threading.Lock()

        def status():
            return {"state": "ready", "model": "fake.gguf", "error": "", "generating": lock.locked(),
                    "loaded_at": 1.0, "pid": None, "settings": {"ctx_size": 4096}, "binary_present": True}

        def stream_chat(messages, params):
            self.seen_messages = messages
            with lock:
                self.cancelled.clear()
                for t in self.tokens:
                    if self.cancelled.is_set():
                        break
                    time.sleep(self.delay)
                    yield {"type": "content", "text": t}
            yield {"type": "done", "stopped": self.cancelled.is_set(),
                   "stats": {"completion_tokens": len(self.tokens), "tokens_per_s": 10.0}}

        eng.status, eng.stream_chat, eng.cancel = status, stream_chat, self.cancelled.set
        type(eng).generating = property(lambda s: lock.locked())
        test.addCleanup(self._restore, eng, orig)

    @staticmethod
    def _restore(eng, orig):
        eng.status, eng.stream_chat, eng.cancel = orig
        type(eng).generating = property(lambda s: s._gen_lock.locked())


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helpers.reset_state()
        cls.srv = helpers.LiveServer().__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.srv.__exit__(None, None, None)

    def setUp(self):
        helpers.reset_state()

    # ---------------------------------------------------------- security
    def test_health_is_public_but_api_needs_token(self):
        code, body, _ = self.srv.request("GET", "/api/health", token=False)
        self.assertEqual((code, body["ok"]), (200, True))
        for path in ["/api/settings", "/api/models", "/api/conversations", "/api/system"]:
            with self.subTest(path=path):
                self.assertEqual(self.srv.request("GET", path, token=False)[0], 401)
        code, _, _ = self.srv.request("GET", "/api/settings", token=False, headers={"X-PocketAI-Token": "wrong"})
        self.assertEqual(code, 401)

    def test_host_and_origin_checks(self):
        self.assertEqual(self.srv.request("GET", "/api/health", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.srv.request("GET", "/api/settings", headers={"Origin": "http://evil.example"})[0], 403)
        ok_origin = f"http://127.0.0.1:{self.srv.port}"
        self.assertEqual(self.srv.request("GET", "/api/settings", headers={"Origin": ok_origin})[0], 200)

    def test_index_injects_token_and_sets_csp(self):
        code, html, headers = self.srv.request("GET", "/", token=False)
        self.assertEqual(code, 200)
        self.assertIn(self.srv.token.value, html)
        self.assertNotIn("{{SESSION_TOKEN}}", html)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        code, js, _ = self.srv.request("GET", "/static/js/app.js", token=False)
        self.assertEqual(code, 200)
        self.assertNotIn("http://", js.replace("http://127.0.0.1", ""))   # no CDN / remote assets

    def test_frontend_has_no_external_assets(self):
        for f in (helpers.REPO / "app" / "frontend").rglob("*"):
            if f.suffix in (".html", ".css", ".js"):
                text = f.read_text(encoding="utf-8")
                with self.subTest(file=f.name):
                    self.assertNotRegex(text, r"(src|href)=[\"']https?://")
                    self.assertNotRegex(text, r"@import\s+url\(\s*['\"]?https?://")

    # ---------------------------------------------------------- settings
    def test_settings_validation(self):
        code, body, _ = self.srv.request("PUT", "/api/settings", {"temperature": 0.2, "theme": "light"})
        self.assertEqual(code, 200)
        self.assertEqual(body["settings"]["theme"], "light")
        self.assertEqual(self.srv.request("PUT", "/api/settings", {"temperature": 9})[0], 400)
        self.assertEqual(self.srv.request("PUT", "/api/settings", {"evil": 1})[0], 400)
        self.assertEqual(self.srv.request("PUT", "/api/settings", {"allowed_roots": ["/no/such/dir/xyz"]})[0], 400)

    # ---------------------------------------------------------- models
    def test_model_endpoints(self):
        code, body, _ = self.srv.request("GET", "/api/models")
        self.assertEqual((code, body["models"]), (200, []))
        self.assertTrue(body["catalog"])
        helpers.write_gguf(config.MODELS_DIR / "tiny.gguf", helpers.QWEN_LIKE, pad=100)
        body = self.srv.request("GET", "/api/models")[1]
        self.assertEqual([m["id"] for m in body["models"]], ["tiny.gguf"])
        self.assertIn("estimated_ram", body["models"][0])
        self.assertEqual(self.srv.request("POST", "/api/models/load", {"model": "../x.gguf"})[0], 404)
        self.assertEqual(self.srv.request("POST", "/api/models/load", {"model": "missing.gguf"})[0], 404)

    def test_memory_warning_requires_force(self):
        helpers.write_gguf(config.MODELS_DIR / "tiny.gguf", helpers.QWEN_LIKE, pad=100)
        orig = ai_engine.LlamaEngine.__dict__["check_memory"]   # keep the staticmethod wrapper
        ai_engine.LlamaEngine.check_memory = staticmethod(lambda info, ctx: (64 * 2**30, 1 * 2**30))
        self.addCleanup(setattr, ai_engine.LlamaEngine, "check_memory", orig)
        if not config.llama_server_path().is_file():
            self.skipTest("llama-server binary not installed")
        code, body, _ = self.srv.request("POST", "/api/models/load", {"model": "tiny.gguf"})
        self.assertEqual(code, 409)
        self.assertTrue(body["memory_warning"])
        self.assertGreater(body["needed"], body["available"])
        self.assertEqual(ai_engine.engine.status()["state"], "stopped")

    def test_params_update(self):
        code, body, _ = self.srv.request("PUT", "/api/models/params", {"ctx_size": 2048, "low_memory": True})
        self.assertEqual((code, body["needs_reload"]), (200, False))
        self.assertEqual(config.load_settings()["ctx_size"], 2048)
        self.assertEqual(self.srv.request("PUT", "/api/models/params", {"ctx_size": 10})[0], 400)

    # ---------------------------------------------------------- chat
    def test_chat_without_model_is_rejected_cleanly(self):
        code, body, _ = self.srv.request("POST", "/api/chat/stream", {"message": "hi"})
        self.assertEqual(code, 409)
        self.assertIn("model", body["detail"].lower())
        self.assertEqual(self.srv.request("GET", "/api/conversations")[1]["conversations"], [])

    def test_chat_input_validation(self):
        for body in [{"message": ""}, {"message": "x", "mode": "../etc"}, {"message": "x", "conversation_id": 0},
                     {"message": "x" * 300000}]:
            with self.subTest(body=str(body)[:40]):
                self.assertIn(self.srv.request("POST", "/api/chat/stream", body)[0], (400, 422))

    def test_streaming_chat_saves_history(self):
        FakeEngine(["Hello", " <think>secret plan</think>", " world", "!"]).install(self)
        events = self.srv.stream({"message": "Say hello", "mode": "coding"})
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "meta")
        self.assertEqual(types[-1], "done")
        content = "".join(e["text"] for e in events if e["type"] == "content")
        reasoning = "".join(e["text"] for e in events if e["type"] == "reasoning")
        self.assertEqual(content, "Hello  world!")
        self.assertEqual(reasoning, "secret plan")
        conv_id = events[0]["conversation_id"]
        conv = self.srv.request("GET", f"/api/conversations/{conv_id}")[1]
        self.assertEqual(conv["mode"], "coding")
        self.assertEqual([m["role"] for m in conv["messages"]], ["user", "assistant"])
        self.assertEqual(conv["messages"][1]["content"], "Hello  world!")
        self.assertEqual(conv["messages"][1]["reasoning"], "secret plan")

        # follow-up in the same conversation sends history + system prompt
        fake = FakeEngine(["ok"])
        fake.install(self)
        self.srv.stream({"message": "again", "conversation_id": conv_id, "mode": "coding"})
        roles = [m["role"] for m in fake.seen_messages]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])

        # rename, search, delete
        self.assertEqual(self.srv.request("PATCH", f"/api/conversations/{conv_id}", {"title": "Greeting"})[0], 200)
        found = self.srv.request("GET", "/api/conversations?q=greet")[1]["conversations"]
        self.assertEqual([c["id"] for c in found], [conv_id])
        self.assertEqual(self.srv.request("DELETE", f"/api/conversations/{conv_id}")[0], 200)
        self.assertEqual(self.srv.request("GET", f"/api/conversations/{conv_id}")[0], 404)

    def test_stop_generation_saves_partial_reply(self):
        FakeEngine([f"t{i} " for i in range(200)], delay=0.02).install(self)
        result = {}
        t = threading.Thread(target=lambda: result.update(events=self.srv.stream({"message": "long"})))
        t.start()
        time.sleep(0.5)
        self.assertTrue(self.srv.request("POST", "/api/chat/stop")[1]["stopped"])
        t.join(timeout=20)
        done = result["events"][-1]
        self.assertTrue(done["stopped"])
        conv = self.srv.request("GET", f"/api/conversations/{done['conversation_id']}")[1]
        saved = conv["messages"][-1]
        self.assertEqual(saved["role"], "assistant")
        self.assertTrue(saved["content"].startswith("t0 t1"))
        self.assertLess(len(saved["content"].split()), 200)
        self.assertTrue(saved["stats"]["stopped"])

    # ---------------------------------------------------------- files
    def test_file_endpoints(self):
        proj = Path(tempfile.mkdtemp(prefix="proj "))
        (proj / "app.py").write_text("def add(a, b):\n    return a + b\n")
        (proj / ".env").write_text("DB_PASSWORD=hunter2\n")
        (proj.parent / f"{proj.name}-outside.txt").write_text("nope")
        code, body, _ = self.srv.request("PUT", "/api/settings", {"allowed_roots": [str(proj)]})
        self.assertEqual(code, 200)
        roots = self.srv.request("GET", "/api/files/roots")[1]["roots"]
        self.assertEqual(len(roots), 1)
        listing = self.srv.request("GET", "/api/files/list?root=0&path=")[1]
        self.assertEqual({e["name"] for e in listing["entries"]}, {"app.py", ".env"})
        self.assertEqual(self.srv.request("GET", "/api/files/list?root=0&path=..")[0], 400)
        self.assertEqual(self.srv.request("GET", "/api/files/list?root=5&path=")[0], 404)
        bad = {"source": "path", "root": 0, "path": f"../{proj.name}-outside.txt"}
        self.assertEqual(self.srv.request("POST", "/api/files/inspect", bad)[0], 400)

        ok = self.srv.request("POST", "/api/files/prepare", {"source": "path", "root": 0, "path": "app.py", "task": "bugs"})
        self.assertEqual(ok[0], 200)
        self.assertEqual(ok[1]["mode"], "debugging")
        self.assertIn("def add", ok[1]["message"])

        env_ref = {"source": "path", "root": 0, "path": ".env", "task": "explain"}
        code, body, _ = self.srv.request("POST", "/api/files/prepare", env_ref)
        self.assertEqual(code, 403)
        self.assertTrue(body["detail"]["sensitive"])
        code, body, _ = self.srv.request("POST", "/api/files/prepare", dict(env_ref, allow_sensitive=True))
        self.assertEqual(code, 200)

        upload = {"source": "upload", "name": "x.bin.txt", "content_b64": base64.b64encode(b"\x00\x01\x02").decode()}
        info = self.srv.request("POST", "/api/files/inspect", upload)[1]
        self.assertFalse(info["allowed"])
        self.assertEqual(self.srv.request("POST", "/api/files/prepare", dict(upload, task="explain"))[0], 400)
        self.assertEqual(self.srv.request("POST", "/api/files/inspect", {"source": "upload", "name": "a.py", "content_b64": "%%%"})[0], 400)

    # ---------------------------------------------------------- system
    def test_system_snapshot(self):
        code, s, _ = self.srv.request("GET", "/api/system")
        self.assertEqual(code, 200)
        for key in ("os", "cpu", "memory", "app_drive", "disks", "engine", "database"):
            self.assertIn(key, s)
        self.assertGreater(s["memory"]["total"], 0)
        self.assertGreater(s["app_drive"]["total"], 0)
        usage = self.srv.request("GET", "/api/system/storage?refresh=true")[1]
        self.assertGreater(usage["total"], 0)


class OfflineTests(unittest.TestCase):
    def test_startup_and_requests_make_no_external_connections(self):
        original, guarded, attempts = helpers.loopback_only_guard()
        socket.socket.connect = guarded
        try:
            helpers.reset_state()
            with helpers.LiveServer() as srv:
                FakeEngine(["offline", " ok"]).install(self)
                for path in ["/", "/api/models", "/api/system", "/api/settings", "/api/conversations"]:
                    self.assertEqual(srv.request("GET", path)[0], 200)
                events = srv.stream({"message": "hi"})
                self.assertEqual(events[-1]["type"], "done")
        finally:
            socket.socket.connect = original
        self.assertEqual(attempts, [], f"unexpected network access: {attempts}")


if __name__ == "__main__":
    unittest.main()
