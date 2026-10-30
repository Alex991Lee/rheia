---
# Tavily 配置教程（随包）
# 给 Gateway Agent：用户要求「配置 Tavily / 网络搜索」时，严格按本文件操作
---

# Tavily 网络搜索 — 配置流程

## 是什么

Tavily 是面向 AI 应用的搜索 API，OpenClaw 里有两种用法：
- 作为通用 `web_search` 工具的 provider
- 作为显式插件工具 `tavily_search` / `tavily_extract`

## 权威来源

- 在线文档：https://docs.openclaw.ai/tools/tavily
- 插件参考：https://docs.openclaw.ai/plugins/reference/tavily
- 本文件已自包含确切 schema，照抄即可，无需再查。

## 官方关键字段（不许改动）

| 项 | 官方值 |
|---|---|
| Plugin id | `tavily` |
| 插件包 | `@openclaw/tavily-plugin` |
| 认证 | `TAVILY_API_KEY` 环境变量，或 config `apiKey` |
| Base URL | `https://api.tavily.com`（默认）；可用 `TAVILY_BASE_URL` 或 config `baseUrl` 覆盖 |

## 安装

```bash
openclaw plugins install @openclaw/tavily-plugin
```

官方原话：安装插件后要重启 Gateway 才会 loaded。

## 配置（确切 schema，照抄）

在 openclaw.json 里写：

```json5
{
  plugins: {
    entries: {
      tavily: {
        enabled: true,
        config: {
          webSearch: {
            apiKey: "tvly-...",             // ← 必须放 config.webSearch 下
            baseUrl: "https://api.tavily.com",
          },
        },
      },
    },
  },
  tools: {
    web: {
      search: {
        provider: "tavily",
      },
    },
  },
}
```

## ⚠ 关键坑（配错会直接写崩 Gateway）

apiKey 必须放在 **`plugins.entries.tavily.config.webSearch.apiKey`**，不能放在
`plugins.entries.tavily.config.apiKey`。

- `config` 下没有 `apiKey` 这个字段，多写会触发官方 schema 校验失败：
  `plugins.entries.tavily.config: invalid config: must not have additional properties: "apiKey"`
- Gateway 会因此直接启动失败（不是「超时」，是「配置校验失败」）。
- Tavily 和 mem9 的 key 位置**不一样**：mem9 是 `config.apiKey`，Tavily 是
  `config.webSearch.apiKey`（多一层 webSearch）。不要拿 mem9 的写法类推。

## API key 解析顺序（官方）

1. `plugins.entries.tavily.config.webSearch.apiKey`
2. `TAVILY_API_KEY`（Gateway 环境变量）

两者都没有时，`tavily_search` / `tavily_extract` 会报 setup error。

## 验证

- 让任一 agent 触发一次 `web_search`，或直接调用 `tavily_search`。
- 若用户未提供 API key，提醒用户去 https://tavily.com 注册获取 key。
