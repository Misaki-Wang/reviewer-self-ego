# review_experiment

按论文遍历 `input_data` 中所有不同 version，运行三个单篇审阅实验，每篇保存一个 `reviews.json`。使用 Python 3.9+ 标准库、已登录且支持权限 profile 的 Codex CLI（本机验证版本 0.154.0）和本机 PDF skill，无须安装新的 Python 包。实际 token 用量汇总模块包含在本目录，不依赖 `check_diff`。输入 PDF 需在本机按下述目录结构准备。

## 运行

```sh
# 查看全量任务：15 papers、39 pairs → 54 versions、13 conditions → 702 reviews
python3 review_experiment/runner.py --dry-run

# 全部版本，实验 1
python3 review_experiment/runner.py --experiment 1

# 全部实验；OTHER_MODEL_NAME 请替换成实验 2 指定的其他作者模型名称
python3 review_experiment/runner.py --other-model "OTHER_MODEL_NAME"

# 一篇论文的全部版本、全部实验
python3 review_experiment/runner.py --paper 2406.03862 --other-model "OTHER_MODEL_NAME"

# 单个版本、单个条件；真实 smoke test
python3 review_experiment/runner.py --paper 2406.03862 --version 1 \
  --condition exp1_quality --output review_experiment/smoke_live --retries 0

# 多篇论文并行，每篇内部仍逐版本、逐条件独立运行
python3 review_experiment/runner.py --experiment 1 --workers 4
```

默认模型和推理配置沿用现有 runner 的 `gpt-5.5 / medium`。用 `--model`、`--effort` 更改；`--other-model` 是作者标签，不是另一次模型调用，也不能与被测模型相同。未指定作者标签时，包含 `exp2_other_llm` 的真实运行会在调用前报错，dry-run 不需要该标签。

`--paper`、`--version`、`--condition`、`--experiment` 可重复指定。也可直接传入 `input_data/<paper_id>/` 或某个 pair 目录；传入 pair 目录时，处理该目录的两个版本。`--limit-papers` 按论文限制，`--replicates` 设置每条件独立重复次数。`--timeout` 为每次调用超时秒数，默认 1800；`--retries` 为失败后的重试次数，默认 1。

## 输入去重和独立审阅

从 `<paper_id>/vX_vY/vX.pdf`、`vY.pdf` 直接发现版本，不依赖 `pairs.json`、`pair.json`，也不重复扫描 `.pdf_cache`。以 `(paper_id, version)` 去重，按版本数字排序。例如 `v1_v2/v2.pdf` 和 `v2_v3/v2.pdf` 产生一个 v2 任务。

每个 version 的原始 PDF 在 Python runner 中读取一次，在该版本的所有条件间复用临时字节快照。硬链接副本通过 inode 去重，不再读取；若副本是不同文件，则校验哈希，相同版本出现内容冲突会报错。不同版本即使 PDF 内容相同，仍保留各自的版本记录。

**去重针对 pair 中重复出现的版本，不合并各实验的审阅调用。** 每个 condition/replicate 都启动独立的 `codex exec --ephemeral`，在新的临时工作目录中读取该版本的 PDF，不提供其他版本或先前评审。因而 LLM 在不同条件下仍需分别阅读材料；不跨条件复用对话、摘要或 OA。实验 1 和实验 3 中性组即使 prompt 相同也分别运行；实验 3 中性组在 3A/3B 内计一次。

模型看到中性文件名 `manuscript.pdf`，路径中没有 paper ID、版本号或条件名。原始 PDF 字节保持不变；若 PDF 内包含作者、时间、版本等信息，runner 不自动匿名化，也不声称已完成匿名化。

## Prompt 保真

`prompts/` 中有一个共用 `system.txt`、13 条独立 user message 和校验用 `manifest.json`。全部从 `main_experiment.html` 的对应 `<code>` 块提取，解码 HTML 实体后，保留文本、标点、空格及段落，不加开场白或总结。

运行读取这些固定文本文件，不读取 HTML。启动时验证每个文件的 SHA-256；不自动重新导出、改写或润色 prompt。校验当前 HTML：

```sh
python3 review_experiment/export_prompts.py --check
```

以后明确修改 HTML 后，手动执行 `python3 review_experiment/export_prompts.py` 才会更新 prompt 快照。

Codex CLI 的内置系统指令仍然存在；页面中的 System message 通过 `developer_instructions` 原文传入，User message 原文经 stdin 传入。这是 CLI 的角色映射，不是直接 API 的 system/user 双消息接口。运行时做两项占位符替换：

- `{PAPER_CONTENT}` → 当前完整 PDF 的本地路径，由 Codex 读取；不截断、不用摘要代替。
- `{OTHER_MODEL_NAME}` → 命令行显式指定的作者模型名。

输出 JSON Schema 与原 prompt 一致：`{"oa": 6, "strengths": ["..."], "weaknesses": ["..."]}`；OA 取 1、3、5、6、8、10，strengths / weaknesses 为文本列表。旧版仅含 OA 的结果不再有效，续跑会重新审阅，不会虚构补齐。

| 实验 | Condition ID |
|---|---|
| 1 | `exp1_quality` |
| 2 | `exp2_self_llm`, `exp2_other_llm`, `exp2_self_human`, `exp2_other_human` |
| 3 中性组 | `exp3_neutral` |
| 3A | `exp3_very_positive`, `exp3_positive`, `exp3_negative`, `exp3_very_negative` |
| 3B | `exp3_years`, `exp3_months`, `exp3_days` |

## PDF 和联网设置

每次调用暴露已安装的 PDF skill，默认自动定位；可用 `--pdf-skill /path/to/pdf/SKILL.md` 指定。原始 PDF 通过中性文件名提供，图表、公式和附录仍可供模型使用本地工具查看。不要在测试期间临时下载依赖。

CLI 使用独立 `review_only` 文件权限 profile，默认 `:root = "deny"`。开放本次临时工作目录、当前 PDF（只读）、PDF skill，以及 `:minimal` / 明确指定的 Python 和 Codex PDF 运行库；不开放仓库、`input_data`、其他论文、历史结果或用户目录。当前 PDF 的符号链接只指向被明确授权的快照；临时目录外的其他文件不会因链接而获得读取权限。模型可以搜索提供的 PDF 及其临时提取文件，不能搜索其他实验材料。运行库属于工具依赖，不作为论文资料。

网络配置为 `permissions.review_only.network.enabled=false`，同时设置 `web_search="disabled"`、`approval_policy="never"`。Apps、plugins、MCP、浏览器、电脑控制、多 agent、memories 和 hooks 关闭，避免其他读取渠道。工具子进程使用受控环境，禁用 login shell 和 shell snapshot。`--strict-config` 使不支持的配置报错；不降级回宽松沙箱。

CLI 自身仍需登录状态和模型服务连接；权限 profile 约束的是被测 agent 的工具操作。`--ignore-user-config` 和 `project_doc_max_bytes=0` 避免用户配置、项目 AGENTS 指令混入；不改写用户配置。权限规则见 [官方说明](https://learn.chatgpt.com/docs/permissions)。

## 结果与续跑

```text
review_experiment/results/
  2406.03862/reviews.json
  2502.08987/reviews.json
  ...
  run_summary.json
```

每篇 JSON 内按 `versions.v1.reviews["exp1_quality/r1"]` 等键保存 OA、strengths、weaknesses、状态、模型配置、请求指纹和每次尝试。各版本还保存源路径列表与 PDF 哈希；调用记录保存实际 user message、原始最终响应、错误、命令执行记录及 CLI 报告的 token 用量。不让模型估计 token 数。临时 PDF、执行日志和工具中间文件在任务后清理。

每次调用后原子写盘。重跑跳过指纹相同且三项输出均有效的完成记录，失败或配置变化自动重算；`--overwrite` 明确重算选中的记录。指纹覆盖 PDF、原始 prompt、模型、推理配置、作者标签、CLI 可执行路径、PDF skill 文本及适配器版本。输出目录加锁，防止两次 runner 同时写入。

`reviews.json` 保留先前运行的其他条件结果；`latest_selection_status` / `latest_selection_counts` 表示**本次选择范围**，不表示所有实验都已完成。`run_summary.json` 是最近一次批次的汇总。失败返回非零退出码；中断停止运行中的 CLI，已完成结果保留。

## Smoke test

```sh
# 离线 subprocess smoke：临时 fixture PDF + 假 CLI，覆盖整篇三版本的 39 条调用
python3 -m unittest review_experiment.test_pipeline -v

# 实际数据目录枚举，不读取 PDF 内容或调用模型
python3 review_experiment/runner.py --dry-run

# 真实 OS 沙箱验证，不调用模型
python3 -m review_experiment.sandbox_smoke

# 真实模型 smoke：上面的单版本、单条件命令
```

离线测试覆盖 prompt 与 HTML 一致、版本数字排序、硬链接单次读取、冲突 PDF、完整条件运行、断点跳过、配置失效、失败重试、非法 OA 与超时。假 CLI 分数仅用于验证 pipeline；与真实 `smoke_live/` 结果分开。

`sandbox_smoke` 使用无敏感内容的临时文件验证：提供的 PDF 可读、工作目录可写、目录外文件与目录枚举被拒绝、符号链接不能越界、PDF 不可修改、网络禁用。macOS 上须从可启动子沙箱的普通终端运行；已有外层受限沙箱可能阻止嵌套沙箱启动。
