# 用 Codex 提取会话记忆（实验）

此分支基于 OpenViking v0.5.5。会话提交后，Codex 根据对话、相关旧记忆和指定的项目材料提出记忆修改。OpenViking 负责解析目标、检查修改、合并并写入。归档、后续合并和其他模块仍使用 OpenViking 的配置。每次提取、补读或修正都可能发起一次模型调用。

## 启用

在运行这份源码的服务环境中安装能执行 `codex exec --help` 的 Codex CLI。实验使用 `--ignore-user-config`、`--ephemeral`、`--output-schema` 和 `--output-last-message`，使用服务进程自己的 Codex 登录身份。用同一账户执行 `codex login status` 可查登录状态。会话及配置的项目材料会发给 Codex 所用的模型服务。

在现有 `ov.conf` 的 `memory` 节点中加入以下字段，重启服务，再提交一段测试会话。服务需要原有的 VLM、存储和检索配置。

```json
{
  "memory": {
    "extraction_enabled": true,
    "session_extractor": "codex",
    "codex_timeout_seconds": 120
  }
}
```

`session_extractor` 是服务级开关。设为 `builtin` 并重启即可恢复默认提取器。Codex 超时、进程失败或返回无效格式时，会话提交的提取任务会报告失败。在任务状态中检查结果，修复问题后重新处理对应的归档。

## 随会话提供设计稿和截图

把以下列表放进同一个 `memory` 节点的 `codex_project_evidence` 字段。账号、用户、会话三个标识要与本次提交一致。路径请换成服务端可读取的真实文件。

```json
[
  {
    "account_id": "studio",
    "user_id": "designer",
    "session_id": "backpack-review",
    "files": ["/srv/game/reviews/decision.md", "/srv/game/reviews/pack-320.png"]
  }
]
```

每次提取最多附带 6 个文件。支持 UTF-8 的 MD、TXT、JSON（每个 64 KB），以及 PNG、JPEG、WebP（每张 4 MB）。文本进入提取上下文，图片作为图像附件提交。原文件保存在项目中，只在提取期间复制图片。需要追溯原图时，请在评审记录中保留项目相对路径、截图时间和需求编号。若允许按需读取旧记忆，可沿用 OpenViking 的 `eager_prefetch=false` 设置。

## 验证

在仓库根目录运行以下测试。默认用独立测试进程模拟 Codex 的回复，覆盖资料范围、截图传递、类型检查、无改动和超时。设置环境变量 `OV_CODEX_LIVE_TEST=1` 后，可用 `-k test_live_game_decision` 运行真实 Codex 案例。案例只发送虚构的背包讨论和一条定稿记录。要比较两种提取方式的效果，可将相同会话和材料分别提交到两份相同起点的测试库，再查询原图、截图目的和最终决定。

```sh
python -m pytest -o addopts= -q tests/session/memory/test_codex_extractor.py
```
