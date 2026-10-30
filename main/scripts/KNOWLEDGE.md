---
# Rheia 项目知识库
# 这份文件注入 Gateway Agent 的 system prompt
# Agent 被问到以下问题时，直接引用此文件回答，勿向用户索要
---

## mem9 持久记忆服务

mem9 已预安装在 OpenClaw 中（`plugins.slots.memory = "mem9"`），无需再次安装。

- **托管 API URL**：`https://api.mem9.ai`
- **自托管 URL**：`http://localhost:8080`
- **认证方式**：`X-API-Key` header，环境变量 `MEM9_API_KEY`
- **注册地址**：https://mem9.ai
- **API key 路径**：`plugins.entries.mem9.config.apiKey`（必须放 config 下，不能放 entry 顶层）

配置示例（openclaw.json）：
```json
{
  "plugins": {
    "slots": { "memory": "mem9" },
    "entries": {
      "mem9": {
        "enabled": true,
        "hooks": { "allowConversationAccess": true },
        "config": {
          "apiUrl": "https://api.mem9.ai",
          "apiKey": "<用户从 mem9.ai 获取的 key>"
        }
      }
    }
  }
}
```

如果用户要求配置 mem9 但未提供 API key，提醒用户先去 https://mem9.ai 注册。

## Tavily 网络搜索服务

Tavily 是 web_search 的 provider（插件包 `@openclaw/tavily-plugin`，plugin id `tavily`）。

- **API key 路径**：`plugins.entries.tavily.config.webSearch.apiKey`（**必须**放 `config.webSearch` 下）
- **baseUrl**：`plugins.entries.tavily.config.webSearch.baseUrl`，默认 `https://api.tavily.com`
- **路由**：`tools.web.search.provider = "tavily"`
- **备用认证**：环境变量 `TAVILY_API_KEY`
- **注册地址**：https://tavily.com

⚠ **致命坑**：Tavily 的 key 放 `config.webSearch.apiKey`，和 mem9 的 `config.apiKey` **不一样**（多一层 webSearch）。照 mem9 的写法把 key 写到 `config.apiKey`，会触发官方 schema 校验失败，Gateway 直接启动失败：
`plugins.entries.tavily.config: invalid config: must not have additional properties: "apiKey"`。

配置示例（openclaw.json）：
```json
{
  "plugins": {
    "entries": {
      "tavily": {
        "enabled": true,
        "config": {
          "webSearch": {
            "apiKey": "tvly-...",
            "baseUrl": "https://api.tavily.com"
          }
        }
      }
    }
  },
  "tools": { "web": { "search": { "provider": "tavily" } } }
}
```

如果用户要求配置 Tavily 但未提供 API key，提醒用户先去 https://tavily.com 注册。

## 随包教程索引

两个插件的完整配置流程有独立教程，随包分发。用户要求配置时，先读对应教程再操作：

- Tavily：`/mnt/c/Program Files/Rheia/runtime/tutorials/tavily-setup.md`
- mem9：`/mnt/c/Program Files/Rheia/runtime/tutorials/mem9-setup.md`

（路径是默认安装目录；若用户改了安装目录，按实际安装目录找 `runtime/tutorials/`。）

## 插件和 Skills 管理规范

OpenClaw 的插件和 skills 通过 CLI 管理，基本命令：

```bash
openclaw plugins install <包名>          # 安装插件
openclaw plugins update <包名>            # 更新插件
openclaw plugins list                      # 列出已安装
openclaw skills install <包名>             # 安装 skill
```

来源：@openclaw/<name>（官方）、ClawHub、GitHub 仓库路径、npm 包。

**重要**：具体包名、可用列表、新功能变化以 OpenClaw 官方文档和 mani.json 为准。
- Scout 维护了 `kernel/manifest/mani.json`，Agent 可读取获取当前 provider/channel/plugin/skill 目录
- 官方文档路径：`OfficialDocument/OpenClaw/`（本地副本）
- 在线文档：https://docs.openclaw.ai
- ClawHub：https://clawhub.ai

当用户要求安装或配置某个插件/skill 时，Agent 应：
1. 先查 mani.json 看是否已有该条目
2. 不确定包名或 API 变化时，建议用户查看官方文档，或自行 web search
3. 不要凭空编造包名

## 预装失败清单（动态段）

下方列出预装失败的 skill/plugin 及其失败原因。这些是壳层预装时没装上的，Agent 应**自行在 WSL 里重装**（`openclaw plugins install <包名> --force` 或 `openclaw skills install <包名>`），装完即完成任务，无需询问用户。

<!-- PREINSTALL_FAILURES_BEGIN -->
（暂无预装失败）
<!-- PREINSTALL_FAILURES_END -->


## 读文件守则（防乱码 —— 硬规则，不可绕过）

**背景**：`.docx / .pdf / .xlsx / .pptx` 都是**二进制容器**（docx 实为 ZIP 包）。
**"当文本硬读"→ 二进制字节变乱码（U+FFFD `�`）→ 脏字节进上下文 → 模型被带坏 → 之后每轮都吐乱码（滚雪球，且**用户喊停也修不回来**，因为坏上下文还在）。**

1. **绝不"当文本硬读"二进制**：
   读到 `\x00`（NUL）或大比例 `�` → **立即停**，**不得把内容写进会话 / 记忆 / 语料**。
2. **必须先抽取**：
   - `.docx / .pdf / .xlsx / .pptx` → **`soffice --headless --convert-to txt`**（LibreOffice 随包自带）；
   - 或对应解析库（python-docx / pypdf）。
3. **抽不出来 → 明说"读不了"**（附文件名 + 格式），
   **绝不允许退化成"当文本硬读"**——那是乱码的唯一入口。
4. **出口闸**：即将发出的内容里 `�` 比例异常 → **丢弃重做**，不写回会话。

（同源风险：脏数据进语料 → 蒸馏出脏人格。**读入口干净，人格才干净。**）
