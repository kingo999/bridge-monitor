// Pass the operator-selected interpreter as argv, never as shell command text.
const { spawn } = require('node:child_process');
const child = spawn(process.env.BRIDGE_MONITOR_TEST_PYTHON || '../.venv/Scripts/python.exe',
  ['tests/server.py'], { stdio: 'inherit', shell: false });
child.on('error', () => process.exit(1));
child.on('exit', code => process.exit(code == null ? 1 : code));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
