// export_conversation.js — 读会话历史（chat.history RPC）的数据源
// 由 restore_backup.py 通过 `wsl -d Ubuntu -- node <本文件> <token> <sessionKey>`
// 调用。复用 openclaw 自带的 ws 库，主项目 Python 零新增依赖。
//
// 职责边界（不硬编）：
//   列会话 key 由 Python 侧从 mani 的 method_index 查 `sessions` 命令执行拿到；
//   这里只做「读正文」——走官方 chat.history RPC（display-normalized 历史）。
//
// 流程：connect(token 直连) → chat.history（读指定 sessionKey 的消息）
//       → 输出 JSON 到 stdout 供 Python 解析。
// 输出：OK <json> 或 ERR <message>

const WS = require('/usr/local/lib/node_modules/openclaw/node_modules/ws');

const token = process.argv[2];
const sessionKey = process.argv[3] || 'agent:main:main';
const limit = parseInt(process.argv[4] || '500', 10);

if (!token) { console.log('ERR no token'); process.exit(1); }

const ws = new WS('ws://127.0.0.1:18789');
let id = 0;
const reqs = {};

function send(method, params) {
  const i = String(++id);
  reqs[i] = method;
  ws.send(JSON.stringify({ type: 'req', id: i, method, params }));
  return i;
}

function fail(msg) { console.log('ERR ' + msg); ws.close(); process.exit(1); }

ws.on('open', () => {
  send('connect', {
    minProtocol: 4, maxProtocol: 4,
    client: { id: 'cli', version: '1.0.0', platform: 'windows', mode: 'cli' },
    role: 'operator',
    scopes: ['operator.read', 'operator.write'],
    caps: [], commands: [], permissions: {},
    auth: { token },
    locale: 'zh-CN',
    userAgent: 'onekey-export-conversation/1.0.0',
  });
});

ws.on('message', (data) => {
  const m = JSON.parse(data.toString());
  if (m.type === 'event') return;
  const method = reqs[m.id];

  if (method === 'connect') {
    if (!m.ok) return fail('connect: ' + JSON.stringify(m.error));
    send('chat.history', { sessionKey, limit });
    return;
  }

  if (method === 'chat.history') {
    if (!m.ok) return fail('chat.history: ' + JSON.stringify(m.error));
    const payload = m.payload || {};
    // 只抽 role + text（content 里 type==="text" 的片段），丢弃 thinking/toolCall
    // 等字段——它们的原始 JSON 可能含坏转义（实测 thinking 字段就有），
    // 整个原样 JSON.stringify 会在 Python 侧 json.loads 崩。
    const messages = (payload.messages || []).map(msg => {
      const role = msg.role || '';
      let text = '';
      const content = msg.content;
      if (Array.isArray(content)) {
        text = content
          .filter(c => c && c.type === 'text' && typeof c.text === 'string')
          .map(c => c.text)
          .join('');
      } else if (typeof content === 'string') {
        text = content;
      }
      return { role, text };
    }).filter(msg => msg.text && msg.text.trim());

    const result = { sessionKey: payload.sessionKey || sessionKey, messages };

    // stdout 有 64KB 缓冲截断风险（实测长会话会断），改走文件：写入 argv[4] 指定的
    // WSL 路径，stdout 只回 OK <路径>，Python 侧从文件读完整内容。
    const outPath = process.argv[5];
    if (outPath) {
      try {
        const fs = require('fs');
        fs.writeFileSync(outPath, JSON.stringify(result), 'utf8');
        console.log('OK ' + outPath);
        ws.close();
        process.exit(0);
        return;
      } catch (e) {
        return fail('write file: ' + e.message);
      }
    }
    console.log('OK ' + JSON.stringify(result));
    ws.close();
    process.exit(0);
    return;
  }
});

ws.on('error', (e) => { console.log('ERR ws: ' + e.message); process.exit(1); });
ws.on('close', () => process.exit(0));
setTimeout(() => { console.log('ERR timeout'); process.exit(1); }, 15000);
