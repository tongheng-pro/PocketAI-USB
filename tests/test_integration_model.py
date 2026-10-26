"""Real inference with llama-server and a real GGUF model.

Runs only when POCKETAI_INTEGRATION=1 and a model is installed in the real models folder:

    set POCKETAI_INTEGRATION=1   (Windows)   /   export POCKETAI_INTEGRATION=1   (Linux)
    <python> -m unittest tests.test_integration_model -v
"""
import os
import threading
import time
import unittest
from pathlib import Path

import helpers
from backend import ai_engine, config, model_manager

REAL_MODELS = helpers.REPO / "models"


def _real_model():
    files = sorted(REAL_MODELS.glob("*.gguf"), key=lambda p: p.stat().st_size)
    return files[0] if files else None


@unittest.skipUnless(os.environ.get("POCKETAI_INTEGRATION") == "1", "set POCKETAI_INTEGRATION=1 to run")
class RealModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = _real_model()
        if cls.model is None or not config.llama_server_path().is_file():
            raise unittest.SkipTest("no real model or llama-server installed")
        cls._orig_dir = config.MODELS_DIR
        config.MODELS_DIR = REAL_MODELS
        cls.settings = dict(config.defaults(), ctx_size=2048, max_tokens=96, temperature=0.2)
        t0 = time.time()
        ai_engine.engine.load(cls.model.name, cls.settings, force=True, wait=True)
        cls.load_seconds = time.time() - t0

    @classmethod
    def tearDownClass(cls):
        ai_engine.engine.unload()
        config.MODELS_DIR = cls._orig_dir

    def generate(self, prompt, **over):
        params = dict(config.effective(self.settings), **over)
        out, reasoning, done = [], [], None
        for ev in ai_engine.engine.stream_chat([{"role": "user", "content": prompt}], params):
            if ev["type"] == "content":
                out.append(ev["text"])
            elif ev["type"] == "reasoning":
                reasoning.append(ev["text"])
            else:
                done = ev
        return "".join(out), "".join(reasoning), done

    def test_1_generates_text_offline(self):
        text, reasoning, done = self.generate("What is 2 + 3? Answer with just the number.", enable_thinking=False)
        print(f"\n  load {self.load_seconds:.1f}s; reply {text!r}; stats {done['stats']}")
        self.assertIn("5", text)
        self.assertEqual(reasoning, "")
        self.assertFalse(done["stopped"])
        self.assertGreater(done["stats"].get("tokens_per_s") or 0, 0)

    def test_2_thinking_toggle(self):
        if "qwen3" not in model_manager.describe(self.model).get("architecture", ""):
            self.skipTest("thinking mode only applies to Qwen3")
        text, reasoning, _ = self.generate("Is 17 prime? One word.", enable_thinking=True, max_tokens=400)
        self.assertTrue(reasoning.strip(), "expected reasoning_content when thinking is enabled")

    def test_3_cancel_mid_generation(self):
        params = dict(config.effective(self.settings), max_tokens=400, enable_thinking=False)
        got = []
        timer = threading.Timer(1.5, ai_engine.engine.cancel)
        timer.start()
        done = None
        for ev in ai_engine.engine.stream_chat([{"role": "user", "content": "Write a long story about a USB drive."}], params):
            if ev["type"] == "done":
                done = ev
            else:
                got.append(ev["text"])
        self.assertTrue(done["stopped"])
        self.assertFalse(ai_engine.engine.generating)
        # engine is still usable afterwards
        text, _, _ = self.generate("Say OK.", enable_thinking=False, max_tokens=8)
        self.assertTrue(text.strip())


if __name__ == "__main__":
    unittest.main()
