# 第 3 题提交信息

- 题号：**3，企业知识问答 Agent**。
- GitHub URL：https://github.com/Cherry287/xiaoying-knowledge-agent
- 可见性：公开，评审可直接读取。
- 最终 commit SHA：请使用提交时 main 分支的完整 SHA；可在 GitHub 提交详情页复制，或按下列命令获取。交付回复另附精确 SHA。

```console
git clone https://github.com/Cherry287/xiaoying-knowledge-agent.git
cd xiaoying-knowledge-agent
git rev-parse HEAD
python -m unittest discover -s tests -v
python scripts/verify_traces.py
```

项目包含源码、合成数据、31 项自动测试、11 组离线场景、7 条交互式模型执行记录，以及题目要求的五份文档。评审入口见 [README](README.md) 和 [评估文档](docs/03-evaluation.md)。

## 如实披露

代码与材料由 AI 辅助生成；候选人个人修改和复跑情况尚无可核实记录，须据实补充。详见 [AI 使用说明](docs/04-ai-usage.md)。

真实 Responses API 自动端到端运行尚未验证：本次环境缺少密钥和模型配置。Mock 只验证流程；stdio 记录由当前 Codex 会话逐步驱动，不能替代自动 API 稳定性验证。以上限制保留在材料中。

本地初始提交 SHA 与 GitHub 最终提交 SHA 可能不同，因为 GitHub 仓库先由用户初始化，上传提交保留该初始化历史。**请提交本仓库最终 main 的 SHA，不要使用旧离线压缩包中的 SHA。** 后续任何修改都会生成新 SHA，需使用最终版本。
