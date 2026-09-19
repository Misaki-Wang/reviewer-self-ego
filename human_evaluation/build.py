"""Build reproducible, label-blinded, standalone human evaluation forms.

Run from any directory: python3 human_evaluation/build.py
Distribute each named HTML in html/human-evaluation/ to its evaluator.
"""
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
import json
import random
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
SOURCE = ROOT / "res/published/check_diff_5conf_20260919"
OUT = ROOT / "html/human-evaluation"
SEED = 20260918
STUDY = "paper-trace-human-v1"
EVALUATOR_NAMES = ["haomin", "xinyue", "han", "kwesi", "dang"]
CONFERENCES = ["iclr2026", "icml2026", "acl2026", "cvpr2026", "eccv2026"]
LABELS = ["major improvement", "minor improvement"]
DIMENSIONS = [
    ("M1", "方法与模型", "Method and model", "算法、架构、目标函数或实际流程是否发生变化；更名和补充说明本身不算方法变化。"),
    ("E1", "数据集与任务覆盖", "Dataset and task coverage", "实际使用的数据集、任务、领域或测试场景；同一任务内的划分或样本数变化归入 E3。"),
    ("E2", "基线与比较方法", "Baselines and comparison methods", "实际参与对应实验的比较方法是否增删或修改；仅新增引用不算。"),
    ("E3", "评估协议与实验设置", "Evaluation protocol and experimental settings", "数据划分、预处理、指标、预算、模型规模或测试流程；仅补充原有设置的信息归入 D1。"),
    ("E4", "消融、鲁棒性与补充分析", "Ablations, robustness and supplementary analyses", "消融、敏感性、分布变化、错误分析或案例分析；以实际检验的问题为准。"),
    ("E5", "统计与不确定性报告", "Statistics and uncertainty reporting", "重复次数、随机种子、置信区间或显著性检验的报告方式；仅数值变化归入 E6。"),
    ("E6", "具体结果", "Concrete results", "数值、排名、趋势或结果条目的变化；实验条件不同不能直接当作同条件下的性能提升。"),
    ("T1", "理论、假设与证明", "Theory, assumptions and proofs", "数学定义、假设、命题、推导或证明；等价记号和排版变化不算理论变化。"),
    ("C1", "核心主张与贡献", "Core claims and contributions", "摘要、引言或结论中的科学主张及其范围；证据增加本身不代表主张改变。"),
    ("C2", "解释与局限", "Interpretation and limitations", "结果解释、机制、适用边界、失败条件或局限；区分观察结果与解释。"),
    ("D1", "技术细节与可复现信息", "Technical details and reproducibility information", "实现、参数、硬件、处理流程及代码或数据获取信息；区分新披露信息与实际设置改变。"),
    ("R1", "相关工作与研究定位", "Related work and research positioning", "引用增删或与已有工作的关系、创新定位变化；编号和书目信息修正属于呈现变化。"),
    ("P1", "语言与文档结构", "Language and document structure", "语言、术语、段落、章节顺序与叙述组织；科学内容变化另在对应维度核验。"),
    ("P2", "图表与版面", "Figures, tables and layout", "图表增删、重绘、合并、位置、图例与版式；同一表格内仅数值变化归入 E6。"),
]


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def safe_embed(value):
    # Script data must not be able to close its HTML element.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def sanitize_description(text):
    """Remove explicit machine verdicts, without treating the prose as verified.

    Additional reviewed sentence edits are stored in neutral_edits.json.
    Unchanged-content descriptions are retained: absence of a change is a
    factual assertion to verify, not an exposed classification field.
    """
    text = re.sub(r"\b(?:major|minor)\s+(change|revision|improvement|deterioration)s?\b", lambda m: "change" if m.group(1) in ("change", "revision") else "difference", text, flags=re.I)
    # Remove degree adjectives only in positive descriptions of revisions.
    # Preserve negation, uncertainty, statistical significance, scientific
    # result claims, and phrases such as 'substantially unchanged'.
    pattern = re.compile(r"\b(?:substantially|modestly) (?=(?:expand|extend|broaden|revis|reorganiz|restructur|redraw|redrawn|redesign|rewrit|rewritten|refram|reposition|reorder|recast|condens|compress|edit|narrow|chang|shift|develop|resiz|qualif|sharp|strengthen)\w*\b)|\b[Ss]ubstantial (?=(?:new |comparative |quantitative |sensitivity, |baseline, |comparison, |compositional, |distributional, |experimental |physical-system |data-curation |geometric |reproducibility |implementation |annotation |documentation |narrative |editorial |mathematical |display |presentation |figure |introduction |portions |results|result sets|findings|analys[ei]s|configuration|interpretations|limitations|rewriting|condensation|changes|appendix|AM[I,]|Phi-2|Qwen3))")
    original = text
    def remove_degree(match):
        prefix = original[:match.start()]
        if re.search(r"\b(?:no|not|without|rather than)(?:\s+\w+){0,2}\s+$", prefix, re.I):
            return match.group(0)
        return "Additional " if match.group(0).startswith("Substantial ") else ""
    text = pattern.sub(remove_degree, text)
    return text.strip()


def build():
    rng = random.Random(SEED)
    used_papers = set()
    sampled = []
    reviewers = [[] for _ in range(5)]
    input_hashes = {}
    edits = json.loads((HERE / "neutral_edits.json").read_text()) if (HERE / "neutral_edits.json").exists() else {}
    translations = json.loads((HERE / "translations_zh.json").read_text())
    translation_sources = json.loads((HERE / "translation_sources.json").read_text())
    # Validate all translations before replacing any distributable files.
    assert set(translations) == set(translation_sources), "Incomplete translation coverage"
    for pair_id, translated in translations.items():
        assert set(translated) == {d[0] for d in DIMENSIONS}, pair_id
        assert all(isinstance(t, str) and re.search(r"[\u4e00-\u9fff]", t) for t in translated.values()), pair_id
    help_en = json.loads((HERE / "dimension_help_en.json").read_text())
    ui_en = json.loads((HERE / "ui_en.json").read_text())
    edit_keys_used = set()
    for ci, conf in enumerate(CONFERENCES):
        pair_path, paper_path = SOURCE / conf / "pairs.jsonl", SOURCE / conf / "papers.jsonl"
        input_hashes[conf] = {"pairs_sha256": digest(pair_path), "papers_sha256": digest(paper_path)}
        papers = {r["arxiv_id"]: r for r in map(json.loads, paper_path.open())}
        pools = {label: defaultdict(list) for label in LABELS}
        for row in map(json.loads, pair_path.open()):
            if row["status"] == "completed" and row["result"]["improvement"]["change"] in LABELS:
                pools[row["result"]["improvement"]["change"]][row["arxiv_id"]].append(row)
        for li, label in enumerate(LABELS):
            ids = sorted(set(pools[label]) - used_papers)
            rng.shuffle(ids)
            chosen = []
            for paper_id in ids[:15]:
                used_papers.add(paper_id)
                rows = sorted(pools[label][paper_id], key=lambda r: r["key"])
                row = rng.choice(rows)
                paper = papers[paper_id]
                pair_id = "P-" + sha256((STUDY + ":" + row["key"]).encode()).hexdigest()[:10].upper()
                versions = {v["version"]: v for v in paper["versions"]}
                descriptions = {}
                edit_log = {}
                for dim, *_ in DIMENSIONS:
                    original = row["result"][dim]["justification"]
                    clean = sanitize_description(original)
                    edit_key = f"{row['key']}:{dim}"
                    if edit_key in edits:
                        clean = edits[edit_key]
                        edit_keys_used.add(edit_key)
                    descriptions[dim] = clean
                    if clean != original:
                        edit_log[dim] = {"original": original, "displayed": clean}
                participant = {"id": pair_id, "title": paper["title"], "arxiv_id": paper_id, "descriptions": descriptions}
                for name in ("before", "after"):
                    v = row[name]
                    participant[name] = {"version": v, "pdf_url": f"https://arxiv.org/pdf/{paper_id}v{v}", "abs_url": f"https://arxiv.org/abs/{paper_id}v{v}", "pages": versions[v].get("page_count", {}).get("value")}
                artifact = row["publication"]["artifacts"]["judgment"]
                assert digest(SOURCE / artifact["path"]) == artifact["sha256"]
                record = {"id": pair_id, "key": row["key"], "arxiv_id": paper_id, "conference": conf, "label": label, "dimension_labels": {d[0]: row["result"][d[0]]["change"] for d in DIMENSIONS}, "source_judgment": artifact, "description_edits": edit_log, "participant": participant, "evaluators": []}
                sampled.append(record)
                chosen.append(record)

            # 5 double-rated pairs form a Hamiltonian cycle. Over the 10
            # conference/category strata every reviewer pair shares 5 papers.
            step = 1 if (ci + li) % 2 == 0 else 2
            cycle = [(i * step + ci + 2 * li) % 5 for i in range(5)]
            for i, record in enumerate(chosen):
                targets = [cycle[i], cycle[(i + 1) % 5]] if i < 5 else [(i - 5) % 5]
                record["rating_count"] = len(targets)
                for ri in targets:
                    record["evaluators"].append(f"E{ri + 1:02}")
                    reviewers[ri].append(record)

    assert edit_keys_used == set(edits), f"Unused edits: {set(edits) - edit_keys_used}"
    assert len(sampled) == len(used_papers) == 150
    assert {r["id"] for r in sampled} == set(translation_sources), "Translation source IDs no longer match the sample"
    assert Counter(r["rating_count"] for r in sampled) == {1: 100, 2: 50}
    shared = Counter(tuple(sorted(r["evaluators"])) for r in sampled if r["rating_count"] == 2)
    assert len(shared) == 10 and set(shared.values()) == {5}, shared
    OUT.mkdir(parents=True, exist_ok=True)
    template = (HERE / "template.html").read_text()
    app = (HERE / "app.js").read_text()
    app = app.replace("__UI__", safe_embed(ui_en))
    forms = []
    for ri, records in enumerate(reviewers):
        rng.shuffle(records)
        assert len(records) == 40
        assert Counter(r["label"] for r in records) == dict.fromkeys(LABELS, 20)
        assert Counter(r["conference"] for r in records) == dict.fromkeys(CONFERENCES, 8)
        assert Counter(r["rating_count"] for r in records) == {1: 20, 2: 20}
        payload = {"study_id": STUDY, "schema_version": 1, "evaluator_id": f"E{ri + 1:02}", "dimensions": [dict(zip(("id", "name", "english", "help"), d)) for d in DIMENSIONS], "pairs": [json.loads(json.dumps(r["participant"])) for r in records]}
        payload["assignment_hash"] = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        payload["evaluator_name"] = EVALUATOR_NAMES[ri]
        # Preserve the original task hash and browser-storage key: English
        # source claims, allocations, options and question meanings are fixed.
        # Track bilingual presentation separately from task identity.
        for dimension in payload["dimensions"]:
            dimension["help_en"] = help_en[dimension["id"]]
        for pair in payload["pairs"]:
            assert pair["descriptions"] == translation_sources[pair["id"]]["descriptions"]
            assert pair["id"] in translations, f"Missing translation: {pair['id']}"
            assert set(translations[pair["id"]]) == set(pair["descriptions"])
            pair["descriptions_zh"] = translations[pair["id"]]
        payload["presentation_version"] = "bilingual-v1"
        payload["presentation_hash"] = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        html = template.replace("__EVALUATOR__", payload["evaluator_name"]).replace("__PAYLOAD__", safe_embed(payload)).replace("__APP__", app)
        filename = f"{payload['evaluator_name']}.html"
        (OUT / filename).write_text(html)
        forms.append({"evaluator_id": payload["evaluator_id"], "evaluator_name": payload["evaluator_name"], "file": filename, "assignment_hash": payload["assignment_hash"], "pair_ids": [r["id"] for r in records], "html_sha256": sha256(html.encode()).hexdigest()})
    manifest = {"study_id": STUDY, "seed": SEED, "source": str(SOURCE.relative_to(ROOT)), "source_hashes": input_hashes, "sampling": "Within each conference, sample 15 distinct papers per overall label, major then minor without replacement across the study; choose one eligible pair uniformly per sampled paper. Five of each 15 are double-rated. Conference-balanced purposive strata, not a prevalence sample.", "shared_counts": {"/".join(k): v for k, v in sorted(shared.items())}, "forms": forms, "pairs": [{k: v for k, v in r.items() if k != "participant"} for r in sampled]}
    (HERE / "organizer-manifest.json").write_text(json_text(manifest))
    with zipfile.ZipFile(OUT / "evaluator-pages.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for form in forms:
            archive.write(OUT / form["file"], form["file"])
    print(json_text({"unique_pairs": len(sampled), "papers": len(used_papers), "assignments": sum(map(len, reviewers)), "double_rated_pairs": 50, "forms": [f["file"] for f in forms], "description_edits": sum(len(r["description_edits"]) for r in sampled), "reviewer_pair_overlap": manifest["shared_counts"]}))


if __name__ == "__main__":
    build()
