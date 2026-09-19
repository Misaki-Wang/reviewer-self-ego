"""Validate the actual generated assignments, source links and blind payloads."""
from collections import Counter
from hashlib import sha256
from itertools import combinations
from pathlib import Path
import json
import re
import unittest
import zipfile

from build import ROOT, HERE, OUT, CONFERENCES, LABELS, DIMENSIONS, safe_embed, sanitize_description

# Original Chinese-only forms: upgrading presentation must preserve task
# identity, original English claims, question meanings and assignment order.
ORIGINAL_ASSIGNMENTS = {
    "E01": "95ab202a96eacc6f3879c7e16de9bea5f37e1e03679297a7d87ed4c2a36ce398",
    "E02": "1d00d620585f438a88191d8fc6db918dac74ff019bb9eff96eeda633acb381d9",
    "E03": "4037fa97f78bc67d52ba6cf944dd46b0bb7f9bd4a08fc9dac8b45e99d7e7608b",
    "E04": "94c76504a73845c8e50a864da904edbf0a1f640b9ef5c1d65564bea20ccd2288",
    "E05": "66cfd5f3fc0ccd35976320d42e0c38b564d15c5bddea76e610a8768809336d52",
}


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((HERE / "organizer-manifest.json").read_text())
        cls.records = {p["id"]: p for p in cls.manifest["pairs"]}
        cls.forms = []
        for f in cls.manifest["forms"]:
            html = (OUT / f["file"]).read_text()
            payload = json.loads(re.search(r'<script id="study-data" type="application/json">(.*?)</script>', html, re.S)[1])
            cls.forms.append((f, html, payload))

    def test_allocation(self):
        records = self.manifest["pairs"]
        self.assertEqual(len(records), 150)
        self.assertEqual(len({r["arxiv_id"] for r in records}), 150)
        self.assertEqual(Counter(r["label"] for r in records), dict.fromkeys(LABELS, 75))
        self.assertEqual(Counter(len(r["evaluators"]) for r in records), {1: 100, 2: 50})
        self.assertEqual(Counter(r["label"] for r in records if len(r["evaluators"]) == 2), dict.fromkeys(LABELS, 25))
        for conf in CONFERENCES:
            self.assertEqual(Counter(r["label"] for r in records if r["conference"] == conf), dict.fromkeys(LABELS, 15))
        for a, b in combinations(self.manifest["forms"], 2):
            self.assertEqual(len(set(a["pair_ids"]) & set(b["pair_ids"])), 5)
        for f, _, payload in self.forms:
            self.assertEqual(len(payload["pairs"]), 40)
            self.assertEqual(len(set(f["pair_ids"])), 40)
            records = [self.records[p["id"]] for p in payload["pairs"]]
            self.assertEqual(Counter(r["label"] for r in records), dict.fromkeys(LABELS, 20))
            self.assertEqual(Counter(r["conference"] for r in records), dict.fromkeys(CONFERENCES, 8))
            self.assertEqual(Counter(r["rating_count"] for r in records), {1: 20, 2: 20})
            for r in records:
                self.assertIn(f["evaluator_id"], r["evaluators"])
            for conf in CONFERENCES:
                self.assertEqual(Counter(r["label"] for r in records if r["conference"] == conf), dict.fromkeys(LABELS, 4))

    def test_source_integrity_and_links(self):
        for conf in CONFERENCES:
            for filename, key in [("pairs.jsonl", "pairs_sha256"), ("papers.jsonl", "papers_sha256")]:
                path = ROOT / self.manifest["source"] / conf / filename
                self.assertEqual(sha256(path.read_bytes()).hexdigest(), self.manifest["source_hashes"][conf][key])
        seen = {}
        for f, html, payload in self.forms:
            self.assertEqual(sha256(html.encode()).hexdigest(), f["html_sha256"])
            presentation = {k: v for k, v in payload.items() if k != "presentation_hash"}
            self.assertEqual(sha256(json.dumps(presentation, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), payload["presentation_hash"])
            original = json.loads(json.dumps(payload))
            for key in ("assignment_hash", "presentation_hash", "presentation_version"):
                original.pop(key)
            for dimension in original["dimensions"]:
                dimension.pop("help_en")
            for pair in original["pairs"]:
                pair.pop("descriptions_zh")
            self.assertEqual(sha256(json.dumps(original, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), payload["assignment_hash"])
            self.assertEqual(payload["assignment_hash"], ORIGINAL_ASSIGNMENTS[f["evaluator_id"]])
            for p in payload["pairs"]:
                r = self.records[p["id"]]
                artifact = r["source_judgment"]
                source = ROOT / self.manifest["source"] / artifact["path"]
                self.assertEqual(sha256(source.read_bytes()).hexdigest(), artifact["sha256"])
                original = json.loads(source.read_text())
                self.assertEqual(original["improvement"]["change"], r["label"])
                self.assertLess(p["before"]["version"], p["after"]["version"])
                for side in ("before", "after"):
                    v = p[side]["version"]
                    self.assertEqual(p[side]["pdf_url"], f"https://arxiv.org/pdf/{p['arxiv_id']}v{v}")
                    # The published catalog retains unresolved page counts.
                    # Never invent a page count or exclude a pair for this.
                    if p[side]["pages"] is not None:
                        self.assertIsInstance(p[side]["pages"], int)
                        self.assertGreater(p[side]["pages"], 0)
                self.assertEqual(set(p["descriptions"]), {d[0] for d in DIMENSIONS})
                for dim, text in p["descriptions"].items():
                    self.assertTrue(text.strip())
                    expected = r["description_edits"].get(dim, {}).get("displayed", original[dim]["justification"])
                    self.assertEqual(text, expected)
                if p["id"] in seen:
                    self.assertEqual(p, seen[p["id"]])
                seen[p["id"]] = p

    def test_blinding_and_standalone_delivery(self):
        forbidden_keys = {"label", "labels", "dimension_labels", "improvement", "change", "rating_count", "evaluators", "conference", "source_judgment"}
        def check_keys(value):
            if isinstance(value, dict):
                self.assertFalse(set(value) & forbidden_keys)
                for sub in value.values():
                    check_keys(sub)
            elif isinstance(value, list):
                for sub in value:
                    check_keys(sub)
        for _, html, payload in self.forms:
            check_keys(payload)
            self.assertNotRegex(html.lower(), r"major improvement|minor improvement|major change|minor change|organizer-manifest")
            self.assertNotRegex(html, r'<script[^>]+src=|<link[^>]+href=|<textarea|type="text"')
            self.assertNotIn("__PAYLOAD__", html)
            self.assertNotIn("__APP__", html)
            self.assertNotIn("__UI__", html)
            self.assertEqual(html.count('<script'), 2)
        with zipfile.ZipFile(OUT / "evaluator-pages.zip") as z:
            self.assertEqual(sorted(z.namelist()), [f"evaluator-{i:02}.html" for i in range(1, 6)])
            for name in z.namelist():
                self.assertEqual(z.read(name), (OUT / name).read_bytes())

    def test_complete_bilingual_materials(self):
        translations = json.loads((HERE / "translations_zh.json").read_text())
        sources = json.loads((HERE / "translation_sources.json").read_text())
        self.assertEqual(set(translations), set(self.records))
        self.assertEqual(set(sources), set(translations))
        self.assertEqual(sum(len(d) for d in translations.values()), 2100)
        for _, _, payload in self.forms:
            self.assertEqual(payload["presentation_version"], "bilingual-v1")
            for dimension in payload["dimensions"]:
                self.assertTrue(dimension["help_en"].strip())
                self.assertNotRegex(dimension["help_en"], r"[\u4e00-\u9fff]")
            for pair in payload["pairs"]:
                self.assertEqual(pair["descriptions"], sources[pair["id"]]["descriptions"])
                self.assertEqual(pair["descriptions_zh"], translations[pair["id"]])
                self.assertEqual(set(pair["descriptions_zh"]), set(pair["descriptions"]))
                for text in pair["descriptions_zh"].values():
                    self.assertRegex(text, r"[\u4e00-\u9fff]")
                    self.assertNotRegex(text, r"待翻译|TODO|PLACEHOLDER")
        ui = json.loads((HERE / "ui_en.json").read_text())
        for value in ui.values():
            self.assertTrue(value.strip())
            self.assertNotRegex(value, r"[\u4e00-\u9fff]")
        for source in ("app.js", "template.html"):
            text = (HERE / source).read_text()
            keys = re.findall(r'tr\("([^"\n]+)"\)', text) + re.findall(r'data-translate="([^"]+)"', text)
            for key in keys:
                self.assertIn(key, ui, (source, key))

    def test_neutralization_preserves_uncertainty(self):
        for text in ["No substantial change is established.", "The tables are not substantially redesigned.", "This is without substantially changing the claim.", "The claims remain substantially unchanged.", "The difference is statistically significant (p = 0.02)."]:
            self.assertEqual(sanitize_description(text), text)
        self.assertEqual(sanitize_description("v2 substantially expands the tables."), "v2 expands the tables.")
        self.assertNotIn("</script>", safe_embed({"x": "</script><script>alert(1)</script>"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
