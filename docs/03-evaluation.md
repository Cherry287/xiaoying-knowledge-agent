# 评估：预期、实际结果和限制

## 证据分层

| 层次 | 本次结果 | 能说明什么 |
|---|---|---|
| 自动测试 | 31/31 通过 | 本地工具、引用边界、执行上限、CLI 和模拟 API 合同符合断言 |
| 离线场景 | 11/11 符合预期 | 确定性 Mock 在实际工具观察下正确完成流程 |
| 交互式模型 | 7 条实际运行记录 | 当前 Codex 会话在观察工具结果后，逐步选择了不同动作 |
| 自动 Responses API | 未运行成功：缺少密钥和模型配置 | 不能证明 API 兼容性、稳定性、延迟、费用或端到端成功率 |
| GitHub CI | 查看 [最新 Actions 运行](https://github.com/Cherry287/xiaoying-knowledge-agent/actions) | 本地报告不替代远端运行结论，以对应 commit 的检查状态为准 |

测试实际报告：[tests.txt](../examples/mock/tests.txt)、[tests.json](../examples/mock/tests.json)。离线场景汇总：[evaluation.json](../examples/mock/evaluation.json)。Trace 核验报告：[trace-verification.json](../examples/trace-verification.json)。

## 验收点对照

| 场景 | 预期 | 实际交互结果 | 记录 |
|---|---|---|---|
| 明确答案 | 读取 P01 后引用完整文本回答 | answered，3 步 | [clear_answer](../examples/live/clear_answer.json) |
| 文档冲突 | 比较 D01/D02，补查后列出双方并请求确认 | conflict，5 步；未擅选 15/30 天 | [conflict](../examples/live/conflict.json) |
| 资料外问题 | 补查仍无证据时说明缺口 | insufficient_evidence，3 步 | [outside](../examples/live/outside.json) |
| 改写相似问题 | 配字能映射到字幕资料 | answered，3 步 | [paraphrase](../examples/live/paraphrase.json) |
| 上下文追问 | 消解“它”，重新检索原文 | answered，3 步 | [context](../examples/live/context.json) |
| 工具临时失败 | 失败不记成功；观察 retryable 后选择重试 | answered，4 步；保留失败事件 | [failure_once](../examples/live/failure_once.json) |
| 工具永久失败 | 停止并保留已成功的读取结果 | incomplete，4 步；D01 留存 | [failure_permanent](../examples/live/failure_permanent.json) |

输入在 [examples/inputs](../examples/inputs/)；每份输出还内嵌了原始 input、数据指纹和完整 Trace。交互记录来源与限制详见 [PROVENANCE](../examples/live/PROVENANCE.md)。

## 边界、资料变更和反例

| 用例 | 预期与实际结果 | 证据 |
|---|---|---|
| 缺少问题 | needs_clarification；不访问文档 | [missing_question](../examples/mock/missing_question.json) |
| 限制为 2 步 | incomplete/MAX_STEPS，保留已执行事件 | [step_limit](../examples/mock/step_limit.json) |
| P01 改为不支持自动字幕 | 回答随当前原文改变，不复用旧答案 | [fact_changed](../examples/mock/fact_changed.json) |
| D02 改为与 D01 相同 | 冲突消失，answered；不再走冲突补查分支 | [conflict_resolved](../examples/mock/conflict_resolved_by_document_change.json) |
| 伪造 quote、只引用部分文本 | finish 被拒绝；不会输出模型伪造事实 | tests 中 GuardrailTests |
| 只读 D01 后想结束 | 因 D02 未读而拒绝 | test_unread_competing_document_blocks_answer |
| 已读两份冲突但只引用一份 | answered 被拒绝，conflict 也不能漏引另一方 | test_cannot_cherry_pick_conflict / test_conflict_requires_both_citations |
| 空语料、坏输入、未知工具 | 缺资料或显式校验失败；不会执行任意工具 | tests 中边界断言 |

资料变更的 `.documents.json` 和 `.input.json` 一同保存，可重新运行。注意：这两项变更用例目前为 Mock 策略验证，尚未做独立模型的盲测，不夸大为真实模型泛化证据。

## 复现

```console
python -m unittest discover -s tests -v
python scripts/evaluate.py
python scripts/verify_traces.py
python -m src.kb_agent --input examples/mock/fact_changed.input.json --documents examples/mock/fact_changed.documents.json
```

Trace 验证器会将每条记录中的动作重新交给真实本地工具执行，比较数据指纹、每步结果、后续动作和最终输出。**重放只能核验记录与代码一致，不证明动作作者、模型身份或重新决策能力。**

配置模型后运行 `python scripts/evaluate.py --provider openai --output-dir examples/openai`，将得到独立 API 记录。断言检查状态和关键引用；仍需要人工判断查询是否合理、回答是否完整。若失败，应保留失败 Trace 并更新本文，不能将离线结果挪到 API 目录冒充。

## 实际遇到的问题和未覆盖部分

首次评估因项目根路径多取了一层 parent 而失败，报 FileNotFoundError；修正为 `Path(__file__).resolve().parents[2]` 后通过。检查 Mock 改写逻辑时发现加入泛化的“产品说明”可能把资料外问题引到无关产品文档，已改为不含现有领域关键词的补查表达。

尚未验证：真实 API 服务端响应、多个模型比较、多次随机运行、提示注入对模型选择的影响、长文切块、权限、并发、断点恢复。7 条交互记录来自参与编写代码的同一 Codex 会话，属于非盲测；没有独立裁判或统计显著性。stdio 的 operator-label 是自行声明，不是可验证模型签名。

当前冲突检查是原文差异检查，可能过度拒答；复杂语义冲突如果被错误分配到不同 policy_key，也可能漏检。关键词词表覆盖有限，可能召回不足或过多。`gaps` 是模型生成的待确认说明，未做完整语义校验；业务答案本身仅渲染验证过的原文。以上限制均保留，不宣称生产可用。
