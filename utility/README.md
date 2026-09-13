# Data preparation

## v1.3 preparation

Use `prepare_v13_pilot.py` for the v1.3 index or an individual source shard.
It requires Python 3.11+ and the existing `pypdf` dependency in
`utility/requirements.txt`. It reads cached, original version-specific PDFs;
it does not download papers, install tools, or invoke a model.

Each paper selects the earliest-valid maximum-length sequence from its complete
frozen version history. Every consecutive selected gap is strictly greater than
2,592,000 seconds (30 days); skipped original versions remain in the metadata.
A selected sequence of `k` versions contributes `k - 1` planned pairs.

```sh
# Inspect the complete ACL2026 scope without creating output or reading PDF contents.
python3 utility/prepare_v13_pilot.py --source v1.3/acl2026.json \
  --pdf-root /path/to/arxiv_pdf --all --dry-run

# Freeze a reproducible, source-balanced sample before inspecting PDF contents.
python3 utility/prepare_v13_pilot.py --source v1.3/index.json \
  --pdf-root /path/to/arxiv_pdf --units 200 --seed 20260911 \
  --output input_data_v13 --plan-only

# Materialize exactly that saved plan from the existing PDF store.
python3 utility/prepare_v13_pilot.py \
  --materialize-plan input_data_v13/selection_manifest.json \
  --output input_data_v13 --pdf-root /path/to/arxiv_pdf
```

The PDF store layout is `<arxiv_id>/<arxiv_id>vN.pdf`. `--all` selects the full
eligible, resolved inventory. `--units N` uses the existing deterministic,
source-balanced sampling rule; `--per-source N` assigns that many distinct papers
per source. The default is five papers per source. `--all` cannot be combined with
either sampling option; `--units` overrides `--per-source`. Source membership is preserved even when a paper appears in more
than one shard; each selected paper contributes its pairs once. Sampling does
not depend on local PDF availability.

`--dry-run` prints counts without creating a run. `--plan-only` saves the exact
paper IDs, selected versions, complete original records, source hashes and
requested PDFs in `selection_manifest.json`. Materialization verifies the fixed
plan and PDF identities/hashes, then creates the comparison layout:

```text
input_data_v13/
  selection_manifest.json
  materialization_manifest.json
  pairs.json
  <arxiv_id>/metadata.json
  <arxiv_id>/vN_vM/vN.pdf
  <arxiv_id>/vN_vM/vM.pdf
  <arxiv_id>/vN_vM/pair.json
```

Missing or invalid selected PDFs are recorded and cause a nonzero exit. The
selected papers are not replaced and no alternative version pairs are created.
After resolving input problems, materialize the same plan again. Use a fresh
output directory for a different selection. The [v1.3 inventory](../v1.3/README.md)
reports planned counts, not guaranteed runnable pairs.

The prepared directories can be passed to the existing comparison runner;
preparation itself never starts comparison calls. Full original metadata stays
outside the isolated pair assessment. Source JSON and original PDFs are read-only.

`source_provenance.py` provides a read-only verifier for callers that freeze code
as well as data. Its `verified_sources(project, worktree, sources, source_commit,
extra_files=())` API requires an explicit immutable commit and verifies exported
deployment manifests or matching local Git bytes. It supports both size/hash
records and the versioned hash-only deployment format with separate v1.3 shard
hashes. Invalid manifests do not fall back to Git. MBZ-specific batch orchestration
remains outside this portable preparation utility.

```sh
python3 -m unittest discover -s utility -p 'test_*.py'
```

## Legacy v1.1 preparation

The original utility and its behavior are retained below for historical inputs.


一个入口完成读取主目录 JSON、下载所有冻结版本 PDF、构建全部相邻版本 pair：

```sh
python3 -m pip install -r utility/requirements.txt
python3 utility/prepare_input_data.py
```

需要 Python 3.11+、`pypdf` 和 `curl`。默认路径根据脚本位置解析，在任意工作目录运行均可。
默认读取 `v1.1.json`，固定种子 `20260911`，抽取 15 篇：ICLR 8、ICML 5、ACL 2。
如果从当前 GitHub main 克隆后只有 `v1.2.json`，先在仓库根目录执行以下命令恢复本次抽样使用的冻结数据；两个版本的 JSON 结构不同，不能直接替换：

```sh
git show v1.1:v1.1.json > v1.1.json
```

全部版本指 JSON 的 `v1…frozen_N`，包括示例三元组跳过的版本；不查询冻结后的新增版本。
N 个版本构建 N−1 个相邻 pair，不过滤时间间隔，不生成跨版本组合。

```text
input_data/
  <arxiv_id>/
    metadata.json
    v1_v2/
      v1.pdf
      v2.pdf
      pair.json
    v2_v3/
      v2.pdf
      v3.pdf
      pair.json
  .pdf_cache/<arxiv_id>/v1.pdf
  sample_metadata.json
  download_manifest.json
  papers.csv
  pairs.json
  README.md
```

pair 中是普通 PDF 文件，优先用硬链接避免重复占用磁盘，不支持时复制；没有符号链接依赖。
请将 PDF 视为只读输入。`pairs.json` 和 `pair.json` 的 PDF 路径均相对于 `input_data/`。
每次运行检查 PDF 结构、页数、可用的首页版本标记及 SHA-256；下载还检查响应文件名和版本 URL。
下载失败返回非零状态，记录错误，只有两端均验证成功才构建 pair。重复运行复用缓存并重试失败项。
更换源数据或样本时请使用新的 `--output` 目录，以免混入旧 pair。

```sh
# 只查看默认 15 篇及预计数量
python3 utility/prepare_input_data.py --dry-run

# 指定论文，仍然下载这些论文的全部冻结版本并配对
python3 utility/prepare_input_data.py --arxiv-ids 2305.14299 --output input_data_custom

# 检查失败重试、缓存复用、pair 完整性及下载版本校验
python3 -m unittest discover -s utility -p 'test_*.py'
```

准备完成后运行 `python3 check_diff/runner.py`。比较流程见 [check_diff](../check_diff/README.md)。
