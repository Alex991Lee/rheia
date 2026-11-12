// L1-105：export_conversation.js












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
