import { readFile, access, readdir } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { scenes } from '../site/scripts/content.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../site');

/** 检查本地页面资源与片段目标，避免发布残留占位图和死链。 */
async function checkPage(name) {
  const html = await readFile(path.join(root, name), 'utf8');
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length, `${name} 存在重复 ID`);
  for (const [, target] of html.matchAll(/\b(?:src|href)="([^"]+)"/g)) {
    if (/^(https?:|data:|mailto:)/.test(target)) continue;
    if (target.startsWith('#')) assert(ids.includes(target.slice(1)), `${name} 中不存在 ${target}`);
    else await access(path.join(root, target.split(/[?#]/)[0]));
  }
  for (const [, target] of html.matchAll(/\b(?:aria-controls|aria-labelledby|data-copy)="([^"]+)"/g)) {
    for (const id of target.split(' ')) assert(ids.includes(id), `${name} 的目标 ${id} 不存在`);
  }
  assert.match(html, /lang="zh-CN"/);
  assert.match(html, /name="viewport"/);
}

for (const name of ['index.html', 'credits.html']) await checkPage(name);
for (const scene of scenes) await access(path.join(root, scene.image));
const css = await readFile(path.join(root, 'styles/main.css'), 'utf8');
for (const [, asset] of css.matchAll(/url\("([^"]+)"\)/g)) await access(path.resolve(root, 'styles', asset));
const html = await readFile(path.join(root, 'index.html'), 'utf8');
assert.match(html, /git clone --branch dev-webui/);
assert.match(html, /uv run qrclaw --help/);
assert.match(html, /api_key:/);
assert.match(css, /prefers-reduced-motion/);
assert.match(html, /id="motion-toggle"/);
const files = await readdir(path.join(root, 'assets'));
assert(files.includes('social.png'), '缺少社交分享图');
assert(!(await readdir(root)).includes('__check.html'), '发布前须移除临时浏览器测量页');
console.log('官网页面、资源、命令和交互目标检查通过。');
