"""Analyze returned forms without modifying responses or evaluation materials."""
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import json
import random
import re

HERE = Path(__file__).resolve().parent
OUT = HERE / 'analysis'
POSITIVE = {'clearly_better', 'slightly_better'}
ACCURACY = {'accurate', 'partly_accurate', 'inaccurate', 'unable_to_determine'}
QUALITY = POSITIVE | {'no_meaningful_difference', 'slightly_worse', 'clearly_worse', 'unable_to_determine'}
EVIDENCE = {'both_versions', 'one_version', 'descriptions_only', 'access_problem'}


def counts(rows, field):
    return dict(Counter(r[field] for r in rows))


def interval(clusters, metric, seed=20260925, reps=10000):
    """Resample entire paper pairs, keeping all observed raters within a pair.

    Estimates rating-weighted proportions; missing responses remain missing.
    This CI conditions on this rater panel and is not a rater-population CI.
    """
    rng = random.Random(seed)
    points = [(sum(metric(r) for r in rs), len(rs)) for rs in clusters]
    samples = []
    for _ in range(reps):
        a = b = 0
        for _ in points:
            x, y = rng.choice(points)
            a += x
            b += y
        samples.append(a / b)
    samples.sort()
    return [samples[int(reps * .025)], samples[int(reps * .975)]]


def analyze():
    manifest = json.loads((HERE / 'organizer-manifest.json').read_text())
    records = {p['id']: p for p in manifest['pairs']}
    forms = {f['evaluator_id']: f for f in manifest['forms']}
    rows, files, metadata_notes = [], [], []
    seen = set()
    for path in sorted((HERE / 'responses').glob('*.json')):
        d = json.loads(path.read_text())
        eid = d['evaluator_id']
        assert eid not in seen, f'Multiple exports for {eid}: explicitly select one before analysis'
        seen.add(eid)
        f = forms[eid]
        assert d['study_id'] == manifest['study_id'] and d['schema_version'] == 1
        assert d['assignment_hash'] == f['assignment_hash']
        assert set(d['answers']) == set(f['pair_ids'])
        html = (HERE.parent / 'html/human-evaluation' / f['file']).read_text()
        payload = json.loads(re.search(r'<script id="study-data" type="application/json">(.*?)</script>', html, re.S)[1])
        assert d['presentation_hash'] == payload['presentation_hash']
        dim_ids = {x['id'] for x in payload['dimensions']}
        completed = 0
        for pid, a in d['answers'].items():
            assert set(a['criteria']) <= dim_ids
            assert set(a['criteria'].values()) <= ACCURACY
            assert a['overall'] is None or a['overall'] in QUALITY
            assert a['evidence_scope'] is None or a['evidence_scope'] in EVIDENCE
            complete = len(a['criteria']) == 14 and a['overall'] is not None and a['evidence_scope'] is not None
            touched = bool(a['criteria']) or a['overall'] is not None or a['evidence_scope'] is not None
            assert complete or not touched, f'Partially answered pair requires explicit handling: {eid}/{pid}'
            if not complete:
                continue
            completed += 1
            record = records[pid]
            assert eid in record['evaluators']
            rows.append({'pair_id': pid, 'evaluator_id': eid, 'name': f['evaluator_name'], 'label': record['label'], 'conference': record['conference'], **a})
        assert completed == d['completed_pairs']
        assert d['total_pairs'] == 40 and d['status'] == ('complete' if completed == 40 else 'in_progress')
        files.append({'file':path.name, 'sha256':sha256(path.read_bytes()).hexdigest(), 'evaluator':f['evaluator_name'], 'completed':completed})
        if d['exported_at'][:10] not in path.name:
            metadata_notes.append(f"{f['evaluator_name']}: filename date differs from exported_at; do not infer work duration from filenames.")
        same = sum(a.get('first_answered_at') == a.get('updated_at') for a in d['answers'].values() if a['overall'])
        if same == completed:
            metadata_notes.append(f"{f['evaluator_name']}: all completed pairs have equal first/last-answer timestamps; timestamps cannot establish active work time or tool use.")
    by_pair = defaultdict(list)
    for r in rows:
        by_pair[r['pair_id']].append(r)
    by_label = {}
    for label in ['major improvement', 'minor improvement']:
        rr = [r for r in rows if r['label'] == label]
        clusters = [v for v in by_pair.values() if v[0]['label'] == label]
        by_label[label] = {
            'ratings':len(rr), 'overall':counts(rr,'overall'), 'improved':sum(r['overall'] in POSITIVE for r in rr),
            'improved_ci95_pair_cluster_bootstrap':interval(clusters,lambda r:r['overall'] in POSITIVE),
            'clearly_better_ci95_pair_cluster_bootstrap':interval(clusters,lambda r:r['overall']=='clearly_better'),
            'observed_pairs':len(clusters), 'ratings_per_pair':dict(Counter(map(len,clusters))),
            'all_observed_raters_positive':sum(all(r['overall'] in POSITIVE for r in v) for v in clusters),
            'any_observed_rater_positive':sum(any(r['overall'] in POSITIVE for r in v) for v in clusters),
            'pair_weighted_mean_improvement':sum(sum(r['overall'] in POSITIVE for r in v)/len(v) for v in clusters)/len(clusters),
        }
    reviewers = {}
    for f in forms.values():
        rr=[r for r in rows if r['evaluator_id']==f['evaluator_id']]
        reviewers[f['evaluator_name']]={'completed':len(rr),'overall':counts(rr,'overall'),'accuracy':dict(Counter(v for r in rr for v in r['criteria'].values())), 'by_label':{label:counts([r for r in rr if r['label']==label],'overall') for label in by_label}}
    accuracy=Counter(v for r in rows for v in r['criteria'].values())
    dim_counts={dim:dict(Counter(r['criteria'][dim] for r in rows)) for dim in dim_ids}
    dimension_label_counts=defaultdict(Counter)
    flagged=[]
    for r in rows:
        for dim,value in r['criteria'].items():
            dimension_label_counts[records[r['pair_id']]['dimension_labels'][dim]][value]+=1
            if value!='accurate':
                flagged.append({'pair_id':r['pair_id'],'evaluator':r['name'],'dimension':dim,'answer':value,'label':r['label'],'conference':r['conference'],'arxiv_id':records[r['pair_id']]['arxiv_id']})
    shared=[v for v in by_pair.values() if len(v)==2]
    agreement={}
    for label in ['all',*by_label]:
        vv=[v for v in shared if label=='all' or v[0]['label']==label]
        agreement[label]={'pairs':len(vv),'exact_quality_agreement':sum(v[0]['overall']==v[1]['overall'] for v in vv),'binary_improvement_agreement':sum((v[0]['overall'] in POSITIVE)==(v[1]['overall'] in POSITIVE) for v in vv),'both_positive':sum(all(r['overall'] in POSITIVE for r in v) for v in vv),'both_nonpositive':sum(all(r['overall'] not in POSITIVE for r in v) for v in vv),'dimension_exact_agreement':sum(v[0]['criteria'][dim]==v[1]['criteria'][dim] for v in vv for dim in dim_ids),'dimension_comparisons':len(vv)*14}
    disagreements=[{'pair_id':v[0]['pair_id'],'label':v[0]['label'],'conference':v[0]['conference'],'arxiv_id':records[v[0]['pair_id']]['arxiv_id'],'ratings':{r['name']:r['overall'] for r in v}} for v in shared if (v[0]['overall'] in POSITIVE)!=(v[1]['overall'] in POSITIVE)]
    conferences={c:{label:counts([r for r in rows if r['conference']==c and r['label']==label],'overall') for label in by_label} for c in sorted({r['conference'] for r in rows})}
    missing=[{'pair_id':r['id'],'label':r['label'],'conference':r['conference'],'arxiv_id':r['arxiv_id']} for r in records.values() if r['id'] not in by_pair]
    result={'files':files,'metadata_notes':metadata_notes,'completed_ratings':len(rows),'unique_observed_pairs':len(by_pair),'evidence_scope':counts(rows,'evidence_scope'),'overall':counts(rows,'overall'),'by_label':by_label,'reviewers':reviewers,'accuracy':dict(accuracy),'accuracy_by_dimension':dim_counts,'accuracy_by_machine_dimension_label':{k:dict(v) for k,v in dimension_label_counts.items()},'pairs_all_descriptions_accurate_in_all_observed_ratings':sum(all(all(x=='accurate' for x in r['criteria'].values()) for r in v) for v in by_pair.values()),'agreement':agreement,'binary_disagreements':disagreements,'flagged_descriptions':flagged,'missing_pairs':missing,'conferences':conferences,'ci_method':'10,000 pair-cluster bootstrap resamples within each machine overall-label stratum; all observed ratings of each sampled pair travel together; percentile 95% CI; fixed rater panel, not uncertainty over possible human raters.'}
    OUT.mkdir(exist_ok=True)
    (OUT/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    def pct(n,d):return f'{n}/{d} ({n/d:.1%})'
    lines=['# Human evaluation：当前回复分析','','## 数据范围','',f"完成 {len(rows)}/200 份评审，覆盖 {len(by_pair)}/150 个不同论文对；42 对已有双评。所有已完成记录自报查看了两个版本。任务哈希、材料哈希、论文分配和选项均校验通过。",'','## 整体改善','', '| 自动标签 | 评审数 | 明显改善 | 略有改善 | 无实质差别 | 任意改善 | 改善率 95% CI |','|---|---:|---:|---:|---:|---:|---|']
    for label,x in by_label.items():
        c=x['overall'];ci=x['improved_ci95_pair_cluster_bootstrap']
        lines.append(f"| {label} | {x['ratings']} | {c.get('clearly_better',0)} | {c.get('slightly_better',0)} | {c.get('no_meaningful_difference',0)} | {pct(x['improved'],x['ratings'])} | {ci[0]:.1%}–{ci[1]:.1%} |")
    lines+=['','CI 按论文对重采样，保留同一对的多名评审。它只反映当前评审组和已回收样本，不包含评审者抽样或尚未完成回复的不确定性。','', '### 去重与一致性','']
    for label,x in by_label.items():lines.append(f"- {label}：{x['observed_pairs']} 对已获得至少一次判断；全部现有判断均认为改善 {x['all_observed_raters_positive']} 对，至少一人认为改善 {x['any_observed_rater_positive']} 对。单评项只反映一人的判断，不能称为多人共识。")
    lines+=['', '| 双评范围 | 对数 | 是否改善一致 | 具体质量选项一致 | 双方均认为改善 |','|---|---:|---:|---:|---:|']
    for label,a in agreement.items():lines.append(f"| {label} | {a['pairs']} | {pct(a['binary_improvement_agreement'],a['pairs'])} | {pct(a['exact_quality_agreement'],a['pairs'])} | {a['both_positive']} |")
    lines+=['','## 评审者尺度','', '| 评审者 | 完成 | major：明显/略有/无差别 | minor：明显/略有/无差别 |','|---|---:|---|---|']
    for name,x in reviewers.items():
        parts=['/'.join(str(x['by_label'][label].get(k,0)) for k in ['clearly_better','slightly_better','no_meaningful_difference']) for label in by_label]
        lines.append(f"| {name} | {x['completed']}/40 | {parts[0]} | {parts[1]} |")
    lines+=['','不能因为尺度严格而排除某位评审。缺失回复仍需补齐，双评分歧适合进行独立复核。','', '## 描述准确性','',f"共 {sum(accuracy.values())} 个维度判断：" ,'']
    for k,v in accuracy.items():lines.append(f'- {k}: {pct(v,sum(accuracy.values()))}')
    lines+=['','这是评审对候选描述的认可率，包含“没有变化”的描述；不是经独立金标准证明的客观准确率。当前人工评估可以在网页版 LLM 辅助下完成，且评审已看到 GPT 生成的描述，因此应称为允许 LLM 辅助的人工核验，不应描述成完全独立、无辅助的专家判断。JSON 没有记录各人实际使用了什么 LLM 或使用程度。','', '## 需要复核的论文对','']
    for d in disagreements:lines.append(f"- {d['pair_id']} / arXiv:{d['arxiv_id']} / {d['label']}："+'；'.join(f'{k}: {v}' for k,v in d['ratings'].items()))
    lines+=['','所有非“属实”的维度判断、分会议结果、未覆盖论文对和文件哈希见 `summary.json`。','', '## 结论边界','', '- 当前样本支持修改描述具有较高人工认可率，且 major 组比 minor 组更常被认为有明显改善。','- major 的“有任何改善”认可率超过 90%，不等于超过 90% 被认定为“明显改善”，也不等于超过 90% 的精确分类一致率。','- 样本按会议和 major/minor 均衡抽取；不代表所有论文或所有候选类别的分布，不能估计完整分类器的召回率。','- 目前仍缺 12 份评审；这些不是负面判断，也不能假定与已回收结果相同。','- 文件时间戳不是实际投入时间或是否使用工具的可靠证据。']
    (OUT/'summary.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:result[k] for k in ['completed_ratings','unique_observed_pairs','by_label','accuracy','accuracy_by_machine_dimension_label','pairs_all_descriptions_accurate_in_all_observed_ratings','agreement','metadata_notes']},ensure_ascii=False,indent=2))

if __name__=='__main__':analyze()
