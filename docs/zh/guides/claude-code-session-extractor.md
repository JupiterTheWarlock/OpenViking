# 用 Claude Code 提取会话记忆

运行本分支的 OpenViking v0.5.5 服务时，在现有 `ov.conf` 的 `memory` 节点设置以下字段，重启服务。会话提交后，Claude Code 读取本轮对话、相关旧记忆和为该会话指定的资料，提出记忆改动。OpenViking 检查并写入改动。

```json
{
  "memory": {
    "extraction_enabled": true,
    "session_extractor": "claude_code",
    "claude_code_timeout_seconds": 120
  }
}
```

服务运行账户需要能执行 `claude auth status` 并使用所选模型。默认沿用该账户的 Claude Code 模型。若使用模型网关，可在 `memory.claude_code_model` 中填写网关实际提供的模型名。此设置仅用于提取会话记忆。

## 给一次评审提供截图和定稿

在 `memory.claude_code_project_evidence` 中按账号、用户、会话三个标识登记文件。将示例路径替换为服务能读取的绝对路径。每次提取最多可指定 6 个文件。图片作为图像传给模型，评审记录作为文本传给模型。

```json
[
  {
    "account_id": "studio",
    "user_id": "designer",
    "session_id": "review",
    "files": ["/srv/game/review/pack-trial.png", "/srv/game/review/approved.md"]
  }
]
```

## 复现截图测试

在仓库根目录设置 `OV_CLAUDE_LIVE_TEST=1`。如需指定测试用模型，再设置 `OV_CLAUDE_TEST_MODEL`。运行以下命令。测试生成一张两行四列的背包图，另在文档中记录需求 UI-17 最终采用三列。它核对真实 Claude Code 提取建议中的文件名、图上列数、定稿列数和需求编号。

```sh
python -m pytest -o addopts= -q tests/session/memory/test_claude_code_extractor.py
```
