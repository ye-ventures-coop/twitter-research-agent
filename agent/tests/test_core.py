"""Smoke-тесты ядра агента без внешних зависимостей.

Запуск из папки agent/:  python -m unittest discover tests
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import researcher
from app.settings import Settings
from app.storage import SeenStore


class TestExtractJsonArray(unittest.TestCase):
    def test_plain_array(self):
        raw = '[{"author": "@a", "text": "t", "url": "https://x.com/a/status/1", "likes": 100}]'
        posts = researcher.extract_json_array(raw)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["author"], "@a")
        self.assertEqual(posts[0]["likes"], 100)

    def test_markdown_fence(self):
        raw = '```json\n[{"author": "@b", "likes": "1.2K"}]\n```'
        posts = researcher.extract_json_array(raw)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["likes"], 1200)

    def test_text_around_array(self):
        raw = 'Вот посты: [{"author": "@c"}] надеюсь помогло'
        posts = researcher.extract_json_array(raw)
        self.assertEqual(len(posts), 1)

    def test_garbage_returns_empty(self):
        self.assertEqual(researcher.extract_json_array("модель не смогла"), [])
        self.assertEqual(researcher.extract_json_array(""), [])
        self.assertEqual(researcher.extract_json_array('{"not": "array"}'), [])
        self.assertEqual(researcher.extract_json_array("[{битый json]"), [])

    def test_metrics_coercion(self):
        raw = json.dumps([{
            "author": "@d",
            "likes": "12,300",
            "views": "1.5M",
            "reposts": None,
        }])
        post = researcher.extract_json_array(raw)[0]
        self.assertEqual(post["likes"], 12300)
        self.assertEqual(post["views"], 1_500_000)
        self.assertEqual(post["reposts"], 0)


class TestFiltersAndFormat(unittest.TestCase):
    CFG = {"min_likes": 50, "min_views": 10000, "min_reposts": 5}

    def test_or_logic(self):
        self.assertTrue(researcher.passes_filters(
            {"likes": 100, "views": 0, "reposts": 0}, self.CFG))
        self.assertTrue(researcher.passes_filters(
            {"likes": 0, "views": 50000, "reposts": 0}, self.CFG))
        self.assertFalse(researcher.passes_filters(
            {"likes": 1, "views": 10, "reposts": 0}, self.CFG))

    def test_format_number(self):
        self.assertEqual(researcher.format_number(999), "999")
        self.assertEqual(researcher.format_number(12300), "12.3K")
        self.assertEqual(researcher.format_number(2_500_000), "2.5M")

    def test_post_key_fallback(self):
        with_url = researcher.post_key({"url": "https://x.com/a/status/1", "author": "@a", "text": "x"})
        self.assertEqual(with_url, "https://x.com/a/status/1")
        no_url = researcher.post_key({"url": "", "author": "@a", "text": "hello world"})
        self.assertEqual(no_url, "@a:hello world")

    def test_format_report_chunks(self):
        posts = [{
            "author": "@a<b>", "text": "t", "summary": "s & <b>",
            "url": "https://x.com/a/status/1",
            "likes": 100, "reposts": 5, "views": 20000, "posted_at": "2h",
        }] * 60
        chunks = researcher.format_report("AI <тема>", posts)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), researcher.MAX_TELEGRAM_CHUNK)
        self.assertIn("AI &lt;тема&gt;", chunks[0])
        self.assertEqual(researcher.format_report("X", []), [])


class TestStorage(unittest.TestCase):
    def test_dedupe(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SeenStore(Path(tmp) / "seen.db")
            self.assertFalse(store.is_seen("k1"))
            store.mark_seen("k1", "topic")
            self.assertTrue(store.is_seen("k1"))
            store.mark_seen("k1", "topic")  # повтор не падает
            self.assertEqual(store.count(), 1)
            store.close()


class TestSettings(unittest.TestCase):
    def test_defaults_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            s = Settings(path)
            self.assertEqual(s.get("interval_hours"), 4)
            s.set("interval_hours", 6)
            s.set("topics", [{"name": "AI", "query": "нейросети"}])
            s2 = Settings(path)  # перечитываем с диска
            self.assertEqual(s2.get("interval_hours"), 6)
            self.assertEqual(s2.get("topics")[0]["name"], "AI")

    def test_unknown_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(Path(tmp) / "settings.json")
            with self.assertRaises(KeyError):
                s.set("nope", 1)

    def test_broken_file_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{битый", encoding="utf-8")
            s = Settings(path)
            self.assertEqual(s.get("min_likes"), 50)


if __name__ == "__main__":
    unittest.main()
