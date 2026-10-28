# 题目 3 · 企业知识问答 Agent

一个可以核验原文、暴露制度冲突并根据工具结果继续查证的 CLI Agent。所有公司制度与产品资料均为合成数据，不代表小影或任何真实公司的政策。

**当前交付状态：** 本地代码和验证材料已完成；31 项自动测试通过；11 组离线场景通过；7 组 Codex 会话逐步驱动的交互记录已运行。**没有配置 API 密钥，因此未完成真实 Responses API 的自动端到端验证。** 提交仓库：[Cherry287/xiaoying-knowledge-agent](https://github.com/Cherry287/xiaoying-knowledge-agent)。题号与最终提交获取方式见 [SUBMISSION.md](SUBMISSION.md)。

## 评审从这里开始

1. 运行离线示例和测试。
2. 阅读 [冲突运行记录](examples/live/conflict.json)：模型读到 30 天和 15 天后，补查范围/优先级，返回 `conflict`。
3. 对照 [正常记录](examples/live/clear_answer.json) 与 [失败恢复记录](examples/live/failure_once.json)：相同工具返回不同结果，产生不同后续动作。
4. 查看 [验收与限制](docs/03-evaluation.md)、[AI 使用说明](docs/04-ai-usage.md) 和 [交互记录来源](examples/live/PROVENANCE.md)。

## 环境、安装与运行

Python 3.11+，本次实测 Python 3.12.7 / Windows 11。仅用标准库，不需要安装第三方包，不需要框架或向量数据库。Linux CI 配置已提供，最新远端运行状态见 [GitHub Actions](https://github.com/Cherry287/xiaoying-knowledge-agent/actions)；本地测试记录与远端 CI 分开核验。

在项目根目录执行（PowerShell、bash 通用）：

```console
python -m src.kb_agent --provider mock --input examples/inputs/clear_answer.json
python -m src.kb_agent --provider mock --input examples/inputs/conflict.json --output work/conflict.json
python -m unittest discover -s tests -v
python scripts/evaluate.py
python scripts/verify_traces.py
```

`mock` 是默认离线入口，**只验证工具、控制循环和保护规则，不证明真实模型决策质量**。每次运行都会标注 `provider`、`is_mock`。复跑 `evaluate.py` 会更新 `examples/mock/` 的实际输出和时间戳，保留 `examples/live/`。

输入文件为 UTF-8 JSON：

```json
{"question":"那它可以修改吗？","history":[{"role":"user","content":"影创支持自动字幕吗？"}]}
```

输出是 JSON，包含 `outcome.status`、`answer`、`citations`（ID、版本日期、原文）、`gaps`、完整 `trace`。未完成返回进程退出码 2；正常完成、明确缺资料或请求澄清返回 0。调用方必须检查任务状态，不能仅凭退出码判断“已答出”。

## 自动模型调用

原生调用 Responses API，每轮将模型选出的工具动作实际执行，再把结果传回模型。不是在固定分支上贴模型日志。模型名称由用户显式指定，不写死可用性或价格。

PowerShell（仅在本机设置密钥，不写入仓库）：

```powershell
$env:OPENAI_API_KEY = '<你的 API 密钥>'
$env:OPENAI_MODEL = '<账号可用且支持 Responses 工具调用的模型 ID>'
python -m src.kb_agent --provider openai --input examples/inputs/conflict.json --output work/api-conflict.json
python scripts/evaluate.py --provider openai --output-dir examples/openai
```

bash 可用 `export OPENAI_API_KEY='...'`、`export OPENAI_MODEL='...'` 设置同名变量。`.env.example` 是配置说明，程序**不自动读取 `.env`**。可选 `OPENAI_BASE_URL` 须为支持 Responses 协议的 HTTPS 服务；使用自定义地址会向该地址发送密钥和问题资料，应由配置者确认其可信。

单次请求超时为 30 秒；每轮最多执行一个工具；默认最多 12 步（含 finish），`--max-steps` 允许 1～50。HTTP/协议错误会明确停止，**不会悄悄回退 Mock**。当前缺配置的实际运行结果见 [api-not-run.json](examples/api-not-run.json)。

接口根据 [OpenAI 官方 Function calling 文档](https://developers.openai.com/api/docs/guides/function-calling) 实现；连续调用会传回 `function_call_output` 和上轮输出。推理上下文仅在内存中转交，未写入公开 Trace。有关无状态连续调用，参见 [官方 Reasoning 文档](https://developers.openai.com/api/docs/guides/reasoning)。

## 无 API 密钥时的交互验证

```console
python -u -m src.kb_agent --provider stdio --operator-label external-model --input examples/inputs/conflict.json --output work/interactive.json
```

CLI 将 `model_request` JSON 发到 stderr，等待 stdin 的一行 JSON 动作，例如：

```json
{"name":"search_documents","arguments":{"query":"报销期限","reason":"先查询相关制度"}}
```

看见返回的 observation 后，再由外部模型或操作员选择下一动作。此入口自身不运行模型，`is_mock:false` 只代表未使用内置 Mock，**不能单独证明输入动作来自模型**。本次 7 条记录由当前 Codex 模型会话逐步输入，未将预先写好的动作脚本作为实时决策。它们不能替代 API 自动运行稳定性评估。重放已有动作也不算新的模型运行。

## 故障和资料替换

```console
python -m src.kb_agent --input examples/inputs/failure_once.json
python -m src.kb_agent --input examples/inputs/failure_permanent.json
python -m src.kb_agent --input examples/inputs/conflict.json --max-steps 2
python -m src.kb_agent --input examples/inputs/paraphrase.json --documents examples/mock/fact_changed.documents.json
```

测试输入可含 `"failures":{"read_document:P01":1}`（失败一次），或 `-1`（永久失败）。故障由工具层执行，不直接设置最终答案。`--documents` 可替换整个语料，重复 ID、缺必需字段和非法日期会被拒绝。

## 目录

```text
src/kb_agent/       CLI、模型适配、决策循环、工具、结果校验
tests/             验收、保护边界、API 合同、CLI 测试
data/              合成原文及检索同义词
examples/inputs/   用户输入及故障注入配置
examples/mock/     11 组离线实际输出、变更资料、测试报告
examples/live/     7 组交互式模型记录与来源说明
docs/              需求、方案、评估、AI 使用和复盘
scripts/           场景评估、Trace 重放核验
```

需求范围与设计取舍见 [01-requirements](docs/01-requirements.md) 和 [02-solution](docs/02-solution.md)。最终提交请按 [SUBMISSION](SUBMISSION.md) 操作。
