// L1-77：set_identity.js




const WS = require('/usr/local/lib/node_modules/openclaw/node_modules/ws');

const token = process.argv[2] || '';
const displayName = process.argv[3] || '';
const avatarBase64 = process.argv[4] || '';
const mime = process.argv[5] || '';

if (!token) { console.log('ERR no token'); process.exit(1); }
if (!displayName && !avatarBase64) { console.log('ERR no displayName/avatarBase64'); process.exit(1); }

const ws = new WS('ws://127.0.0.1:18789');
let id = 0;
const reqs = {};
let profileId = '';

function send(method, params) {
  const i = String(++id);
  reqs[i] = method;
  ws.send(JSON.stringify({ type: 'req', id: i, method, params }));
  return i;
}

ws.on('open', () => {
  send('connect', {
    minProtocol: 4, maxProtocol: 4,
    client: { id: 'cli', version: '1.0.0', platform: 'windows', mode: 'cli' },
    role: 'operator',
    scopes: ['operator.read', 'operator.write'],
    caps: [], commands: [], permissions: {},
    auth: { token },
    locale: 'zh-CN',
    userAgent: 'onekey-persona/1.0.0',
  });
});

ws.on('message', (data) => {
  let m;
  try { m = JSON.parse(data.toString()); } catch { return; }
  if (m.type === 'event') return;
  const method = reqs[m.id];

  if (method === 'connect') {
    if (m.ok) { send('users.self', {}); }
    else { console.log('ERR connect: ' + JSON.stringify(m.error)); ws.close(); }
    return;
  }

  if (method === 'users.self') {
    if (m.ok) {
      profileId = m.payload?.profile?.id || m.payload?.id || '';
      if (!profileId) { console.log('ERR no profileId'); ws.close(); return; }
      if (displayName) {
        send('users.setDisplayName', { profileId, displayName });
      } else {
        send('users.setAvatar', { profileId, mime, avatarBase64 });
      }
    } else {
      console.log('ERR self: ' + JSON.stringify(m.error));
      ws.close();
    }
    return;
  }

  if (method === 'users.setDisplayName' || method === 'users.setAvatar') {
    if (m.ok) { console.log('OK'); }
    else { console.log('ERR ' + JSON.stringify(m.error)); }
    ws.close();
  }
});

ws.on('error', (e) => { console.log('ERR ws: ' + e.message); process.exit(1); });
ws.on('close', () => process.exit(0));
