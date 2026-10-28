# 这些记录如何产生

日期：2026-10-28，Asia/Shanghai。来源：当前项目开发的 Codex 会话。

每条记录都通过下列真实 CLI 进程运行：

```console
python -u -m src.kb_agent --provider stdio --operator-label codex-assistant-interactive --input examples/inputs/<case>.json --output examples/live/<case>.json
```

CLI 先发出模型请求和可用工具。Codex 向运行中的 stdin 写入一个 JSON 动作，等待实际 observation，再选择后续动作。所有文件由 Agent 程序在结束时写出，未手填工具结果或改动最终状态。

| 记录 | 后续动作的依据 |
|---|---|
| clear_answer | 命中并读取 P01，原文支持编辑，结束 |
| conflict | D01=30 天，D02=15 天；补查范围/优先级仍无解，列双方冲突 |
| outside | 初次零命中后改为福利/保障检索，再次零命中，说明缺资料 |
| paraphrase | 配字归一为字幕并命中 P01，读取后回答 |
| context | 结合历史将“它”理解为自动字幕，重新读取原文 |
| failure_once | 读取返回临时错误后，选择保留搜索结果并只重试读取 |
| failure_permanent | 已读 D01，D02 返回不可重试故障，保留部分证据并停止 |

限制：stdio 只是传输方式，不能认证输入动作来自何种模型。`operator-label` 是自行声明，不是第三方证明。本次未获得服务端模型 ID、usage 或 Responses response_id，因此这些元数据为空。此会话参与了设计和测试，已知语料，不是独立盲测。

这些记录可以展示本次模型会话根据工具结果选择动作，但不能替代自动 API 接入验证或证明对未知问题的总体成功率。使用脚本重放这些动作只能核验工具结果，不应声称是新一轮模型推理。
