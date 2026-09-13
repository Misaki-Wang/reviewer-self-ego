# check_diff

用 Codex CLI 比较两个版本的原始 PDF。默认 **gpt-5.5 / medium**，4 对并发。Codex 使用已安装的 PDF skill，自行选择具体读取工具。每个 CLI 使用独立临时状态目录，避免并发初始化冲突；通过链接复用本机登录文件和 PDF skill。

```sh
python3 -m pip install -r check_diff/requirements.txt

# 后台跑全部 pairs，四路并发；终端或对话退出后继续
python3 check_diff/runner.py --background --workers 4

# 查看后台进度
tail -f /tmp/llm_ego_check_diff.log

# 前台跑全部 input_data pairs
python3 check_diff/runner.py

# 一对
python3 check_diff/runner.py input_data/2406.03862/v1_v2

# 一篇论文的全部 pairs
python3 check_diff/runner.py --paper 2406.03862

# 查看输入与输出路径
python3 check_diff/runner.py --dry-run
```

需要 Python 3.9+ 和已登录的 Codex CLI。输入可为 `input_data/`，或其中一个 `<arxiv_id>/v1_v2/` 目录。Runner 直接寻找目录中的 `v1.pdf`、`v2.pdf`，不依赖 `pair.json` 或汇总索引。

所有 compare 均禁止联网检索：CLI 显式设置 `web_search="disabled"`（关闭实时和缓存网页搜索），并使用 `workspace-write` 沙箱、`sandbox_workspace_write.network_access=false` 和 `approval_policy="never"` 禁止模型执行的 shell 命令联网及申请放行。比较只以指定的本地 PDF 为证据，不访问 arXiv、代码仓库、引用文献或在线评审，不在比较过程中下载依赖；请提前准备本地 PDF 读取工具。PDF 内的指令仅作为文档内容处理。CLI 自身仍需连接模型服务来完成推理，这与模型工具的外部检索分开。

每对保存判断结果和独立的执行记录；批次结束后另保存用量汇总：

```text
output/<arxiv_id>/diff_v1_v2.json
output/<arxiv_id>/diff_v1_v2.execution.json
output/token_usage.json
```

JSON 开头是独立的 `prelim`（preliminary assessments，前置评估）对象，包含 A1（可比性与材料完整性）和 A2（各版是否有原创实证结果）。其后是 M1、E1–E6、T1、C1、C2、D1、R1、P1、P2 共 14 个变化维度。A1/A2 使用自己的判断体系，不参与 major/minor/maybe/no change 分级。

- A1：`value` 为 `comparable`、`partially_comparable` 或 `unable_to_determine`，附简短 `justification`。
- A2：分别记录 `before` 和 `after`，各有 `value`（`yes`、`no`、`uncertain`）和简短 `justification`。
- 14 个变化维度各包含：

```json
{
  "change": "minor change",
  "justification": "Adds hyperparameter details; the settings are unchanged."
}
```

`change` 只取 `major change`、`minor change`、`maybe`、`no change`。`justification` 用简短英文说明，不限定字数或句数。major/minor 按各维度内的变化幅度判断；maybe 表示变化或其重要性尚不明确；两版均无该类内容归为 no change。

14 维的 `change` 和整体 `improvement.change` 均以小写保存。读取模型回复、保存结果和检查续跑时，只规范化标签的大小写，再严格校验。结构完整且仅大小写不同的历史结果可直接跳过，不再次调用模型，也不改写原文件；未知标签、缺失字段和旧版 `overall` 结构不做语义迁移，仍需重算。理由文本与 A1/A2 字段原样保留。

任务 prompt 位于 `prompts/compare.md`，runner 将其中的占位符替换为该 pair 的真实文件名和绝对路径，并在开头写明比较方向及 A2 before/after 对应的文件。独立维度定义位于 `prompts/rubric.md`。HTML 不参与运行、prompt 构建或结果校验。

JSON 最后追加独立的顶层 `improvement` 对象，包含 `change` 和英文 `justification`。这是基于两版完整 PDF（含附录）、A1/A2 和前面变化分析的整体质量判断，不拆分为 14 个维度，不替代原有评价，也不按各维度投票或累加。同一次 compare 完成全部判断，不增加模型调用。

`improvement.change` 严格取 `major improvement`、`minor improvement`、`no meaningful change`、`minor deterioration`、`major deterioration` 或 `maybe`，新写出的标签统一小写。理由说明关键的前后证据、质量影响及重要利弊；改动多、主张更强或报告性能更高不自动等于质量改善。`no meaningful change` 需要足够证据确认没有实质净质量差异；材料不足、影响不明或利弊并存且无法可靠判断净方向时用 `maybe`，并说明原因。`maybe` 是不确定出口，不是中间档。

各维度分别判断；同一修订可以影响多个维度，但每项需要有对应的变化依据。补充方法或实验细节与实际改变方法或设置分开判断；新增结果与新增统计报告方式分开判断；理论变化与直觉解释分开判断；重命名、搬移和重排先对应原有内容。Justification 使用普通英文文本，可引用章节、图表或 PDF 文件名，不包含工具引用标记或本地路径。具体边界以 `prompts/rubric.md` 为准。

进度、总耗时和预计剩余时间显示在 tqdm；每对耗时和错误通过 `tqdm.write` 打印。CLI 读取过程的中间文件放在系统临时目录，任务结束后清理。执行记录和 token 汇总与判断结果分开保存。

后台进程独立运行，PID 记录在 `/tmp/llm_ego_check_diff.pid`，可用 `kill -TERM $(cat /tmp/llm_ego_check_diff.pid)` 停止。`--log-file` 可指定进度日志位置。每个 worker 会循环领取下一对，直到队列处理完毕。

重复运行会跳过已有且格式有效的结果；无效结果自动重算。修改模型、prompt 或输入 PDF 后，用 `--overwrite` 重算。`--workers` 控制并发，`--timeout` 默认每次 1800 秒，`--retries` 默认失败重试一次。失败返回非零退出码，Ctrl+C 停止运行中的 Codex 进程，已完成结果保留。

缺少 `improvement` 或仍使用 `overall` 而非 `prelim` 的旧 JSON 不符合新 schema，之后运行到对应 pair 时会按现有逻辑自动重算；只有新结果生成并通过校验后才替换原文件。

```sh
python3 -m unittest discover -s check_diff -p 'test_*.py'
```

## Actual token usage

The runner requests the Codex CLI JSON event stream and saves the unmodified `usage` objects from `turn.completed` events. It never asks the model to estimate its own token consumption. Token telemetry is separate from the scientific judgment, including its prelim assessments, fourteen change dimensions, and overall improvement assessment.

Each `diff_vX_vY.execution.json` includes the requested model settings, timestamps, every attempt (including failures and retries), raw reported usage, and a `token_usage` summary. The batch-level `output/token_usage.json` summarizes attempts made during that invocation and links the pair execution records. Previously completed pairs skipped on resume are listed but do not count as new token consumption.

`token_usage.reported_totals` contains:

- `input_tokens`: all reported input tokens, including cached input.
- `cached_input_tokens` and `cache_write_input_tokens`: separately reported cache counters, when available.
- `output_tokens`: all reported output tokens, including reasoning output.
- `reasoning_output_tokens`: the reported reasoning subset, when available.
- `non_cached_input_tokens`: input minus cached input, only when both counters are present for every reported turn.
- `total_tokens`: input plus output. Cached input and reasoning output are subsets and are not added again.

Missing counters remain `null`. `status` is `complete` when every attempted call reports valid input/output counters, `partial` when only some consumption is reported, and `unavailable` when attempted calls provide no valid usage. An invocation that makes no model calls is `not_run` with zero consumption. `attempts_without_reported_usage`, per-field report counts, and telemetry errors expose coverage gaps. A timeout or disconnect can consume tokens before usage is returned, so a partial total is only the observed consumption, not a billing total. Retrying a failed call adds its reported usage rather than replacing it.

The CLI's intermediate reading files and raw event stream remain temporary. The exact reported usage objects, attempt outcomes, and totals are retained in the execution JSON after cleanup. Reusing an older judgment without an execution record cannot recover its historical usage; the batch summary lists such pairs under `pairs_without_execution_records`. `--overwrite` replaces both the judgment and its execution record, so use a new output directory when retaining a separate experiment history.
