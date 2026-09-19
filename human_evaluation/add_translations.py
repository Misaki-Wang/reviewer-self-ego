"""Append assistant-written translations from stdin; no translation service."""
import json
import sys
from pathlib import Path

directory = Path(__file__).resolve().parent
source = json.loads((directory / "translation_sources.json").read_text())
target = directory / "translations_zh.json"
current = json.loads(target.read_text()) if target.exists() else {}
incoming = json.load(sys.stdin)
for pair_id, texts in incoming.items():
    assert pair_id in source, pair_id
    keys = list(source[pair_id]["descriptions"])
    assert len(texts) == len(keys) == 14, (pair_id, len(texts))
    assert all(isinstance(text, str) and text.strip() for text in texts)
    current[pair_id] = dict(zip(keys, texts))
target.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n")
print(f"Translated {len(current)}/{len(source)} pairs; {sum(map(len,current.values()))} descriptions.")
