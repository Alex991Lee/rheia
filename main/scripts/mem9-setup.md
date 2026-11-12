---
# mem9 配置教程（随包）
# 给 Gateway Agent：用户要求「配置 mem9 / 记忆」时，严格按本文件操作
---

# mem9 持久记忆服务 — 配置流程

## 是什么

mem9 是给 AI agent 的持久记忆服务，OpenClaw 通过 `@mem9/mem9` 插件接入。

## 权威来源

- 官方 npm 包：https://www.npmjs.com/package/@mem9/mem9
- 官方仓库 README：https://github.com/mem9-ai/mem9
- 注册：https://mem9.ai

## 官方关键字段

| 项 | 官方值 |
|---|---|
| 插件包 | `@mem9/mem9` |
| 槽位 | `plugins.slots.memory = "mem9"`（**缺了这条 mem9 不生效**） |
| 托管 API URL | `https://api.mem9.ai` |
| 认证 | `X-API-Key` header，环境变量 `MEM9_API_KEY` |

## 安装

```bash
openclaw plugins install @mem9/mem9
```

## 配置（确切 schema，照抄）

在 openclaw.json 里写：

```json5
{
  plugins: {
    slots: { memory: "mem9" },
    entries: {
      mem9: {
        enabled: true,
        hooks: { allowConversationAccess: true },  // OpenClaw 4.23+ 自动上传对话
        config: {
          apiUrl: "https://api.mem9.ai",
          apiKey: "<用户从 mem9.ai 获取的 key>",   // ← 在 config 下（和 Tavily 不同）
        },
      },
    },
  },
}
```

## ⚠ 关键坑

- apiKey 放 **`plugins.entries.mem9.config.apiKey`**（在 `config` 下），不能放 entry 顶层
  `plugins.entries.mem9.apiKey`。
- 注意和 Tavily 的区别：mem9 是 `config.apiKey`，Tavily 是 `config.webSearch.apiKey`。
- 槽位 `plugins.slots.memory = "mem9"` 必须写，缺了插件不会生效。

## config 字段（官方 schema）

`apiUrl`（mnemo-server URL）、`apiKey`（首选凭据，走 `/v1alpha2/mem9s/...` + `X-API-Key` header）、
`provisionToken`（一次性建号 token，只在还没配 apiKey 时用）、
`defaultTimeoutMs`（默认 8000）、`searchTimeoutMs`（默认 15000）、`debug`。

## 验证

- 若用户未提供 API key，提醒用户先去 https://mem9.ai 注册。
- 配置生效以官方文档/插件 README 为准，不要凭印象改。
