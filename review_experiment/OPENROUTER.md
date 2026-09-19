# OpenRouter 单篇 PDF review

独立入口：`python3 -B -m review_experiment.openrouter_runner`。
默认模型为 `google/gemini-3.5-flash-lite`，默认 prompt 包为 `review_experiment/prompts_5conf/`。
不更改原有 Codex runner、prompt 文本或源数据，也不把已有 reviews 发送给模型。

## 输入

默认输入是项目的 `data_5conf/anonymized/pdf/`。支持：

```text
<root>/<conference>/<paper_id>/<paper_id>vN.pdf
<root>/<conference>/<paper_id>/vN.pdf
<root>/<conference>/<paper_id>/vX_vY/vN.pdf
```

同一论文版本的重复文件会校验内容并去重。指定 `--conf`、`--paper`、
`--limit-papers` 可缩小范围；`--latest-only` 取每篇最高版本；否则选中全部版本。
也可直接传单个 PDF 或单篇目录。无会议层的输入需 `--source cvpr2026` 等显式来源。
默认数据目录下的 CVPR 特例由 `data_5conf/MOCK_DATA_MANIFEST.json` 映射，不根据论文内容猜会议。
自定义映射使用 `--input-manifest`，其 files 条目中的 original_pdf_path / mock_anonymized_pdf_path 相对于项目根目录。

本地模拟集内的 PDF 是未匿名化 arXiv 原稿；脚本不进行匿名化。

## 先检查范围

以下命令不需要密钥、不联网、不写输出，也不调用模型：

```sh
python3 -B -m review_experiment.openrouter_runner \
  --output res/openrouter-inventory --latest-only --dry-run
```

当前会选中 20 篇、20 个最新版本、3 种条件，共计划 60 次请求。

## 一篇论文的真实测试

从项目根目录运行；终端会隐藏输入 API key，不将其写入文件或命令参数：

```sh
python3 -B -m review_experiment.openrouter_runner \
  --output res/openrouter-gemini35-new-test \
  --response-format json_object \
  --paper 2401.18034 --latest-only --ask-api-key
```

也可以事先设置环境变量 `OPENROUTER_API_KEY`，并省略 `--ask-api-key`。
代码和日志不保存 Authorization 头。不要把密钥填入脚本或提交到 Git。

只测中性条件时追加 `--condition exp1_quality`；未指定条件时，运行
`exp1_quality`、`exp3_positive`、`exp3_negative` 三种条件。
`--experiment 3` 包含共享中性 baseline、正向、负向。

输入、输出、模型均可调整，例如：

```sh
python3 -B -m review_experiment.openrouter_runner \
  data_5conf/anonymized/pdf/acl2026/2401.18034/2401.18034v3.pdf \
  --output res/openrouter-single-pdf --condition exp1_quality \
  --model google/gemini-3.5-flash-lite --ask-api-key
```

目前顺序执行、每条件一个 replicate。默认 `--effort medium`、`--max-tokens 8192`、
`--timeout 180`、`--retries 1`；不指定 temperature 时使用模型默认值。
条件顺序由 `--order-seed`（默认 0）决定，写入版本记录。

## 请求与输出

每次请求只包含：原始会议 system prompt、当前 user prompt、单篇完整 PDF。
仅将 `{PAPER_CONTENT}` 替换为中性附件名 `manuscript.pdf`；原始文件路径和论文 ID
不进入消息。PDF 以 base64 上传，使用 OpenRouter 的 native PDF 输入，不截断、不转为纯文本。
模型必须在 OpenRouter 目录声明支持 file 和 structured_outputs；其他模型也可通过 `--model` 指定，
但不自动切换 OCR 或降级输出格式。请求无聊天历史、工具调用或联网搜索配置。

默认 `--response-format json_schema` 在 API 层附带完整 JSON Schema。
Gemini 3.5 Flash-Lite 当前实测在该模式返回空对象，可显式选择 `--response-format json_object`，
仅要求 API 输出 JSON，原始 prompt 不变。此参数写入运行配置，脚本不会自动切换模式。
两种模式均在本地严格验证恰好为 `oa`、`strengths`、`weaknesses`；
OA 必须符合当前会议的取值，截断或拒绝响应不计成功。

```text
<output>/
  run_config.json                      # 固定模型/参数/prompt manifest/脚本哈希
  run_summary.json                     # 本次范围、完成数、用量与费用
  <paper_id>/reviews.json              # 与旧 runner 一致的版本/条件组织方式
  attempts/<paper_id>/vN/<condition>/attempt-001/
    request.json                       # 精确 prompt/参数，省略 PDF base64 和密钥
    response.json                      # API 响应，包括返回的原始 usage
    receipt.json                       # 状态、耗时、响应 ID、实际模型/提供商
    error.json                         # HTTP 错误时保存脱敏响应
```

`reviews.json` 保留 `format_version: 1`、`paper_id`、`source_id`、
`versions.vN.reviews.<condition>/r1`、`status`、`result`、`attempts` 等旧结构。
`token_usage` 改为 OpenRouter 实际返回的 prompt/completion/total tokens 和 cost；
原始嵌套 usage 留在每次 attempt，不能把这些字段与 Codex CLI usage 格式混用。
缺失的用量/费用为 null，并单独记录缺失次数，已知小计不冒充完整总数。

重复运行相同配置会跳过已完成且请求指纹相同的结果，并继续失败项。
模型、prompt、脚本或采样配置变化要求使用新输出目录；已选版本 PDF 变化也会拒绝复用。
不要与旧 Codex runner 共用输出目录。输出与输入不得重叠；同一输出目录同时只允许一个 runner。

只对超时、网络错误或 HTTP 408/429/500/502/503/504 重试；等待超过 60 秒的 Retry-After
会留待下次运行。401/402/403 停止整批；其他失败保留记录，最终返回非零退出码。

## 当前验证记录

- 单元测试覆盖原生 PDF 请求、精确 prompt、OA 校验、结果保存、恢复、重试、未知 usage、鉴权失败与密钥脱敏。
- 全部五个会议的 20 篇最新版本 inventory 成功，计划三条件共 60 次。
- 最新真实验证：`res/openrouter-gemini35-paper-json-20260919/`。
  使用新 key、`--response-format json_object` 和默认 medium，2401.18034v3 三条件全部成功；
  中性 OA=3.5、正向 OA=4、负向 OA=3。完整结果在 `2401.18034/reviews.json`，
  可读预览在 `review_preview.md`。这只是单篇流程验证，不能据此推断模型的总体偏差。
- `res/openrouter-gemini35-paper-smoke-20260919/` 保留此前 json_schema 模式三个空对象失败响应与费用。
  最小文本请求同样复现 json_schema 返回空对象、json_object 返回完整字段；尚未确定上游具体缺陷。
- 早期 `res/openrouter-gemini35-flash-lite-smoke-20260919/` 使用旧 key 时为 HTTP 403，保留作历史记录。

离线测试：

```sh
python3 -B -m unittest tests.review_experiment.test_openrouter_runner -v
```

官方接口依据：[模型](https://openrouter.ai/google/gemini-3.5-flash-lite)、
[PDF 输入](https://openrouter.ai/docs/guides/overview/multimodal/pdfs)、
[Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)。
