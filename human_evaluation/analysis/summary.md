# Human evaluation：当前回复分析

## 数据范围

完成 200/200 份评审，覆盖 150/150 个不同论文对；50 对已有双评。所有已完成记录自报查看了两个版本。任务哈希、材料哈希、论文分配和选项均校验通过。

## 整体改善

| 自动标签 | 评审数 | 明显改善 | 略有改善 | 无实质差别 | 任意改善 | 改善率 95% CI |
|---|---:|---:|---:|---:|---:|---|
| major improvement | 100 | 72 | 27 | 1 | 99/100 (99.0%) | 96.8%–100.0% |
| minor improvement | 100 | 15 | 71 | 14 | 86/100 (86.0%) | 78.6%–92.7% |

CI 按论文对重采样，保留同一对的多名评审。它只反映当前评审组和已回收样本，不包含评审者抽样或尚未完成回复的不确定性。

### 去重与一致性

- major improvement：75 对已获得至少一次判断；全部现有判断均认为改善 74 对，至少一人认为改善 74 对。单评项只反映一人的判断，不能称为多人共识。
- minor improvement：75 对已获得至少一次判断；全部现有判断均认为改善 62 对，至少一人认为改善 68 对。单评项只反映一人的判断，不能称为多人共识。

| 双评范围 | 对数 | 是否改善一致 | 具体质量选项一致 | 双方均认为改善 |
|---|---:|---:|---:|---:|
| all | 50 | 44/50 (88.0%) | 31/50 (62.0%) | 43 |
| major improvement | 25 | 25/25 (100.0%) | 16/25 (64.0%) | 25 |
| minor improvement | 25 | 19/25 (76.0%) | 15/25 (60.0%) | 18 |

## 评审者尺度

| 评审者 | 完成 | major：明显/略有/无差别 | minor：明显/略有/无差别 |
|---|---:|---|---|
| haomin | 40/40 | 11/8/1 | 5/7/8 |
| xinyue | 40/40 | 14/6/0 | 3/16/1 |
| han | 40/40 | 18/2/0 | 4/16/0 |
| kwesi | 40/40 | 17/3/0 | 2/15/3 |
| dang | 40/40 | 12/8/0 | 1/17/2 |

不能因为尺度严格而排除某位评审。双评分歧适合进行独立复核。

## 描述准确性

共 2800 个维度判断：

- accurate: 2760/2800 (98.6%)
- partly_accurate: 36/2800 (1.3%)
- inaccurate: 2/2800 (0.1%)
- unable_to_determine: 2/2800 (0.1%)

这是评审对候选描述的认可率，包含“没有变化”的描述；不是经独立金标准证明的客观准确率。当前人工评估可以在网页版 LLM 辅助下完成，且评审已看到 GPT 生成的描述，因此应称为允许 LLM 辅助的人工核验，不应描述成完全独立、无辅助的专家判断。JSON 没有记录各人实际使用了什么 LLM 或使用程度。

## 需要复核的论文对

- P-40F84DF27F / arXiv:2512.14266 / minor improvement：dang: slightly_better；haomin: no_meaningful_difference
- P-4F5B4E3621 / arXiv:2507.03197 / minor improvement：han: slightly_better；haomin: no_meaningful_difference
- P-DBA7C8B88B / arXiv:2508.03306 / minor improvement：han: slightly_better；haomin: no_meaningful_difference
- P-15AA5D444A / arXiv:2509.23681 / minor improvement：haomin: no_meaningful_difference；kwesi: slightly_better
- P-AC8F5D883F / arXiv:2601.04025 / minor improvement：haomin: no_meaningful_difference；kwesi: slightly_better
- P-B0E1B7929A / arXiv:2505.13614 / minor improvement：kwesi: no_meaningful_difference；xinyue: slightly_better

所有非“属实”的维度判断、分会议结果、未覆盖论文对和文件哈希见 `summary.json`。

## 结论边界

- 当前样本支持修改描述具有较高人工认可率，且 major 组比 minor 组更常被认为有明显改善。
- major 的“有任何改善”认可率超过 90%，不等于超过 90% 被认定为“明显改善”，也不等于超过 90% 的精确分类一致率。
- 样本按会议和 major/minor 均衡抽取；不代表所有论文或所有候选类别的分布，不能估计完整分类器的召回率。
- 当前完成 200/200 份评审，缺失 0 份；缺失项不作为负面判断。
- 文件时间戳不是实际投入时间或是否使用工具的可靠证据。
