'use strict';
// Run by the computer's owner from a normal Windows session, never through an agent sandbox.
// Only the two named agents' documented Windows sandbox implementation is changed.
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..', '..');
const ids = ['god', 'zaza-mvpmyn3b'];
const checkOnly = process.argv.includes('--check');
const allowed = new Set(['--check']);
for (const arg of process.argv.slice(2)) if (!allowed.has(arg)) throw new Error('Unknown argument');
const changes = ids.map(id => {
  const file = path.join(root, 'hive', 'agents', id, '.codex', 'config.toml');
  const original = fs.readFileSync(file, 'utf8');
  const section = /^\[windows\][ \t]*\r?\n(?:(?!^\[)[\s\S])*/m.exec(original);
  if (!section) throw new Error('Expected [windows] section missing: ' + id);
  const matches = [...section[0].matchAll(/^sandbox\s*=\s*"(elevated|unelevated)"[ \t]*(?:#[^\r\n]*)?$/gm)];
  if (matches.length !== 1) throw new Error('Expected one recognized sandbox mode: ' + id);
  const match = matches[0];
  const start = section.index + match.index;
  const replacement = match[0].replace('"elevated"', '"unelevated"');
  return {id, file, original, mode:match[1], updated:original.slice(0,start)+replacement+original.slice(start+match[0].length)};
});
if (checkOnly) {
  for (const c of changes) console.log(c.id + ': ' + c.mode + ' -> unelevated (preview only)');
  process.exit(0);
}
const suffix = '.before-uac-fix-' + new Date().toISOString().replace(/[:.]/g,'-') + '.bak';
for (const c of changes) {
  if (c.mode === 'unelevated') { console.log(c.id + ': already unelevated'); continue; }
  if (fs.readFileSync(c.file,'utf8') !== c.original) throw new Error('Config changed during preparation: ' + c.id);
  const backup = c.file + suffix;
  fs.copyFileSync(c.file, backup, fs.constants.COPYFILE_EXCL);
  fs.writeFileSync(c.file, c.updated, 'utf8');
  if (fs.readFileSync(c.file,'utf8') !== c.updated) throw new Error('Config verification failed: ' + c.id);
  console.log(c.id + ': Windows sandbox set to unelevated. Backup: ' + backup);
}
console.log('Restart Munder Difflin once to reload both agent sessions. No Windows UAC, firewall, approval policy or workspace root settings were changed.');