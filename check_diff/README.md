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

每对只保存一个文件：

```text
output/<arxiv_id>/diff_v1_v2.json
```

JSON 开头是独立的 `overall` 对象，包含 A1、A2。其后是 M1、E1–E6、T1、C1、C2、D1、R1、P1、P2 共 14 个变化维度。A1/A2 使用自己的判断体系，不参与 Major/minor/maybe/no change 分级。

- A1：`value` 为 `comparable`、`partially_comparable` 或 `unable_to_determine`，附简短 `justification`。
- A2：分别记录 `before` 和 `after`，各有 `value`（`yes`、`no`、`uncertain`）和简短 `justification`。
- 14 个变化维度各包含：

```json
{
  "change": "minor change",
  "justification": "Adds hyperparameter details; the settings are unchanged."
}
```

`change` 只取 `Major change`、`minor change`、`maybe`、`no change`。`justification` 用简短英文说明，不限定字数或句数。Major/minor 按各维度内的变化幅度判断；maybe 表示变化或其重要性尚不明确；两版均无该类内容归为 no change。

任务 prompt 位于 `prompts/compare.md`，runner 将其中的占位符替换为该 pair 的真实文件名和绝对路径，并在开头写明比较方向及 A2 before/after 对应的文件。独立维度定义位于 `prompts/rubric.md`。HTML 不参与运行、prompt 构建或结果校验。

各维度分别判断；同一修订可以影响多个维度，但每项需要有对应的变化依据。补充方法或实验细节与实际改变方法或设置分开判断；新增结果与新增统计报告方式分开判断；理论变化与直觉解释分开判断；重命名、搬移和重排先对应原有内容。Justification 使用普通英文文本，可引用章节、图表或 PDF 文件名，不包含工具引用标记或本地路径。具体边界以 `prompts/rubric.md` 为准。

进度、总耗时和预计剩余时间显示在 tqdm；每对耗时和错误通过 `tqdm.write` 打印。CLI 读取过程的中间文件放在系统临时目录，任务结束后清理。输出目录中只有结果 JSON。

后台进程独立运行，PID 记录在 `/tmp/llm_ego_check_diff.pid`，可用 `kill -TERM $(cat /tmp/llm_ego_check_diff.pid)` 停止。`--log-file` 可指定进度日志位置。每个 worker 会循环领取下一对，直到队列处理完毕。

重复运行会跳过已有且格式有效的结果；无效结果自动重算。修改模型、prompt 或输入 PDF 后，用 `--overwrite` 重算。`--workers` 控制并发，`--timeout` 默认每次 1800 秒，`--retries` 默认失败重试一次。失败返回非零退出码，Ctrl+C 停止运行中的 Codex 进程，已完成结果保留。

```sh
python3 -m unittest discover -s check_diff -p 'test_*.py'
```
