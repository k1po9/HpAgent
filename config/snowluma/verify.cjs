// Local startup guard. Never print tokens, account IDs, config, or message input.
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
try {
  const bot = process.env.ONEBOT_EXPECTED_SELF_ID || '';
  if (!/^[1-9][0-9]{4,19}$/.test(bot)) throw new Error();
  const secret = '/run/secrets/onebot_access_token';
  if (fs.statSync(secret).mode & 0o077) throw new Error();
  const token = fs.readFileSync(secret, 'utf8').trim();
  if (!token) throw new Error();
  const dir = '/app/data/config';
  const selected = `onebot_${bot}.json`;
  if (fs.readdirSync(dir).some(name => /^onebot_.*\.json$/.test(name) && name !== selected)) throw new Error();
  for (const name of ['onebot.json', selected]) {
    if (fs.statSync(path.join(dir, name)).mode & 0o077) throw new Error();
    const cfg = JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'));
    const net = cfg.networks;
    if (cfg.mode !== 'snapshot' || !net ||
        !['httpServers', 'httpClients', 'wsServers'].every(key => Array.isArray(net[key]) && net[key].length === 0) ||
        !Array.isArray(net.wsClients) || net.wsClients.length !== 1) throw new Error();
    const ws = net.wsClients[0];
    if (ws.name !== 'hpagent' || ws.enabled === false || ws.url !== 'ws://hpagent:8082/onebot/v11' ||
        ws.role !== 'Universal' || ws.accessToken !== token || ws.messageFormat !== 'array' ||
        ws.reportSelfMessage !== false || ws.reconnectIntervalMs !== 5000 ||
        cfg.statusCommand?.enabled !== false || cfg.historySync?.enabled !== false ||
        !Array.isArray(cfg.notifications?.channelIds) || cfg.notifications.channelIds.length !== 0) throw new Error();
  }
  const release = JSON.parse(fs.readFileSync('/app/runtime/release.json', 'utf8'));
  const pkg = JSON.parse(fs.readFileSync('/app/runtime/package.json', 'utf8'));
  const qq = cp.execFileSync('dpkg-query', ['-W', '-f=${Version}', 'linuxqq'], {encoding:'utf8'});
  if (release.version !== '2.2.0' || pkg.version !== '2.2.0' || release.target !== 'linux-x64' ||
      process.version !== 'v24.22.1' || qq !== '3.2.32-52194') throw new Error();
  console.log('SnowLuma version and effective OneBot configuration verified.');
} catch {
  console.error('SnowLuma startup refused: initialize/recheck the selected bot configuration and pinned image.');
  process.exit(1);
}
