"""Configuration, model discovery, database and chat-service tests."""
import json
import unittest

import helpers  # noqa: F401  (sets POCKETAI_HOME first)
from backend import chat_service, config, database, model_manager


class ConfigTests(unittest.TestCase):
    def setUp(self):
        helpers.reset_state()

    def test_defaults_when_missing(self):
        s = config.load_settings()
        self.assertEqual(s, config.defaults())
        self.assertEqual(s["ctx_size"], 4096)

    def test_save_and_reload(self):
        config.save_settings({"temperature": 0.3, "theme": "light", "allowed_roots": ["x"]})
        s = config.load_settings()
        self.assertEqual(s["temperature"], 0.3)
        self.assertEqual(s["theme"], "light")
        self.assertEqual(s["allowed_roots"], ["x"])
        self.assertTrue(config.SETTINGS_FILE.exists())

    def test_validation_errors(self):
        bad = [{"nope": 1}, {"ctx_size": 100}, {"ctx_size": "4096"}, {"temperature": 3},
               {"low_memory": "yes"}, {"theme": "pink"}, {"threads": 1.5}, {"ctx_size": True},
               {"active_model": "../secret.gguf"}, {"allowed_roots": "C:/"}]
        for upd in bad:
            with self.subTest(upd=upd), self.assertRaises(config.SettingsError):
                config.validate(upd)

    def test_corrupt_file_falls_back(self):
        config.SETTINGS_FILE.write_text("{not json")
        self.assertEqual(config.load_settings(), config.defaults())
        config.SETTINGS_FILE.write_text(json.dumps({"ctx_size": 1, "temperature": 0.5}))
        s = config.load_settings()
        self.assertEqual(s["ctx_size"], 4096)      # invalid value ignored
        self.assertEqual(s["temperature"], 0.5)    # valid value kept

    def test_low_memory_caps(self):
        eff = config.effective(dict(config.defaults(), low_memory=True, ctx_size=8192, max_tokens=2048))
        self.assertEqual(eff["ctx_size"], 2048)
        self.assertEqual(eff["max_tokens"], 512)
        self.assertEqual(eff["history_messages"], 8)

    def test_paths_are_relative_to_home(self):
        self.assertEqual(config.ROOT, helpers.TEST_HOME.resolve())
        self.assertTrue(str(config.DB_FILE).startswith(str(helpers.TEST_HOME.resolve())))


class ModelDiscoveryTests(unittest.TestCase):
    def setUp(self):
        helpers.reset_state()

    def test_discovers_and_reads_metadata(self):
        helpers.write_gguf(config.MODELS_DIR / "tiny.gguf", helpers.QWEN_LIKE, pad=1000)
        helpers.write_gguf(config.MODELS_DIR / "family" / "other.gguf", {"general.architecture": "llama"})
        helpers.write_gguf(config.MODELS_DIR / "mmproj-vision.gguf", {"general.architecture": "clip"})
        (config.MODELS_DIR / "notes.txt").write_text("x")
        models = {m["id"]: m for m in model_manager.discover()}
        self.assertEqual(set(models), {"tiny.gguf", "family/other.gguf"})
        m = models["tiny.gguf"]
        self.assertTrue(m["valid"])
        self.assertEqual(m["display_name"], "Tiny Test Model")
        self.assertEqual(m["quantization"], "Q4_K_M")
        self.assertEqual(m["context_train"], 40960)
        self.assertEqual(m["n_layer"], 28)
        self.assertEqual(models["family/other.gguf"]["display_name"], "other")

    def test_invalid_file_is_flagged_not_fatal(self):
        (config.MODELS_DIR / "broken.gguf").write_bytes(b"NOTAGGUF" * 10)
        (config.MODELS_DIR / "truncated.gguf").write_bytes(b"GGUF\x03\x00\x00\x00")
        models = model_manager.discover()
        self.assertEqual(len(models), 2)
        self.assertTrue(all(not m["valid"] and m["error"] for m in models))

    def test_resolve_rejects_traversal(self):
        helpers.write_gguf(config.MODELS_DIR / "ok.gguf", helpers.QWEN_LIKE)
        self.assertTrue(model_manager.resolve_model("ok.gguf").is_file())
        for bad in ["../settings.json", "..\\x.gguf", "/etc/passwd", "", "sub/../../x.gguf", "missing.gguf",
                    "C:/Windows/x.gguf"]:
            with self.subTest(bad=bad), self.assertRaises(FileNotFoundError):
                model_manager.resolve_model(bad)

    def test_memory_estimate_grows_with_context(self):
        p = helpers.write_gguf(config.MODELS_DIR / "tiny.gguf", helpers.QWEN_LIKE, pad=1000)
        info = model_manager.describe(p)
        small = model_manager.estimate_memory(info, 1024)
        big = model_manager.estimate_memory(info, 8192)
        # 28 layers * 8 kv heads * 256 dims * 2 bytes per token
        self.assertEqual(big - small - (8192 - 1024) * 32 * 1024, 28 * 8 * 256 * 2 * (8192 - 1024))

    def test_catalog_entries_are_complete(self):
        for c in model_manager.CATALOG:
            self.assertRegex(c["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(c["file"].endswith(".gguf"))
            self.assertGreater(c["size"], 100 * 1024**2)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        helpers.reset_state()

    def test_conversation_lifecycle(self):
        c = database.create_conversation("First chat", "coding")
        database.add_message(c["id"], "user", "hello")
        database.add_message(c["id"], "assistant", "hi there", reasoning="r", model="m", stats={"tokens_per_s": 5})
        conv = database.get_conversation(c["id"])
        self.assertEqual([m["role"] for m in conv["messages"]], ["user", "assistant"])
        self.assertEqual(conv["messages"][1]["stats"], {"tokens_per_s": 5})
        self.assertTrue(database.rename_conversation(c["id"], "Renamed"))
        self.assertEqual(database.get_conversation(c["id"])["title"], "Renamed")
        self.assertTrue(database.delete_conversation(c["id"]))
        self.assertIsNone(database.get_conversation(c["id"]))
        with database.connect() as conn:   # cascade removed the messages
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_search_titles_and_content_with_wildcards(self):
        a = database.create_conversation("Laravel routes")
        b = database.create_conversation("Other")
        database.add_message(b["id"], "user", "my value is 100% done_now")
        database.create_conversation("Unrelated")
        self.assertEqual([c["id"] for c in database.list_conversations("laravel")], [a["id"]])
        self.assertEqual([c["id"] for c in database.list_conversations("100%")], [b["id"]])
        self.assertEqual(database.list_conversations("1%0"), [])  # % is literal, not a wildcard
        self.assertEqual([c["id"] for c in database.list_conversations("done_now")], [b["id"]])
        self.assertEqual(len(database.list_conversations("")), 3)

    def test_recent_messages_order_and_limit(self):
        c = database.create_conversation("x")
        for i in range(10):
            database.add_message(c["id"], "user" if i % 2 == 0 else "assistant", f"m{i}")
        self.assertEqual([m["content"] for m in database.recent_messages(c["id"], 3)], ["m7", "m8", "m9"])

    def test_invalid_role_and_title(self):
        c = database.create_conversation("x")
        with self.assertRaises(ValueError):
            database.add_message(c["id"], "admin", "x")
        with self.assertRaises(ValueError):
            database.rename_conversation(c["id"], "   ")


class ChatServiceTests(unittest.TestCase):
    def setUp(self):
        helpers.reset_state()

    def test_think_splitter_across_chunks(self):
        s = chat_service.ThinkSplitter()
        out = []
        for chunk in ["Hel", "lo <thi", "nk>plan", "ning</th", "ink> world", "<"]:
            out += s.feed(chunk)
        out += s.flush()
        content = "".join(t for k, t in out if k == "content")
        reasoning = "".join(t for k, t in out if k == "reasoning")
        self.assertEqual(content, "Hello  world<")
        self.assertEqual(reasoning, "planning")

    def test_modes_and_system_prompts(self):
        ids = {m["id"] for m in chat_service.list_modes()}
        self.assertTrue({"general", "coding", "debugging", "documentation"} <= ids)
        self.assertIn("Laravel", chat_service.system_prompt("coding"))
        for bad in ["../secret", "nope", "", "Coding"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                chat_service.system_prompt(bad)

    def test_history_is_trimmed_to_budget(self):
        c = database.create_conversation("x")
        for i in range(30):
            database.add_message(c["id"], "user" if i % 2 == 0 else "assistant", f"message {i} " + "x" * 600)
        database.add_message(c["id"], "user", "latest question")
        settings = dict(config.defaults(), ctx_size=2048, max_tokens=512, history_messages=50)
        msgs, trimmed = chat_service.build_messages(c["id"], "general", settings)
        self.assertTrue(trimmed)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["role"], "user")        # history starts with a user turn
        self.assertEqual(msgs[-1]["content"], "latest question")
        total = sum(chat_service.estimate_tokens(m["content"]) for m in msgs)
        self.assertLessEqual(total, 2048 - 512)

    def test_oversized_latest_message_is_truncated_not_dropped(self):
        c = database.create_conversation("x")
        database.add_message(c["id"], "user", "y" * 50000)
        settings = dict(config.defaults(), ctx_size=1024, max_tokens=256)
        msgs, _ = chat_service.build_messages(c["id"], "general", settings)
        self.assertEqual(len(msgs), 2)
        self.assertIn("truncated", msgs[1]["content"])
        self.assertLess(len(msgs[1]["content"]), 50000)


if __name__ == "__main__":
    unittest.main()
