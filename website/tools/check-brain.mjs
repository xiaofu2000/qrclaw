import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';

/** 验证发布使用官方原文件，并检查样片导航指向真实页面和段落。 */
const root = new URL('../site/', import.meta.url);
const model = await readFile(new URL('assets/brain/model.glb', root));
assert.equal(model.toString('ascii', 0, 4), 'glTF', '大脑资源必须是 GLB 文件');
assert.equal(model.readUInt32LE(8), model.length, '大脑资源长度不完整');
assert.equal(createHash('sha256').update(model).digest('hex'), '1f6a19277ae876a0ccb3f63fcff5c7fe3c6039894a158cbc78c658f055445e5a', '大脑资源与 AH 官方下载版本不一致');
const gltf = JSON.parse(model.toString('utf8', 20, 20 + model.readUInt32LE(12)));
assert.equal(gltf.meshes.length, 3, '大脑、脑干和小脑网格必须完整');
assert.ok(gltf.buffers.every(buffer => !buffer.uri), '模型不应依赖外部缓冲文件');

const html = await readFile(new URL('brain-preview.html', root), 'utf8');
for (const [, link] of html.matchAll(/(?:src|href)="([^"]+)"/g)) {
  if (/^https?:/.test(link)) continue;
  const [file, fragment] = link.split('#');
  const target = await readFile(new URL(file || 'brain-preview.html', root));
  if (fragment) assert.ok(target.toString().includes(`id="${fragment}"`), `导航目标不存在：${link}`);
}
assert.ok(html.includes('https://sketchfab.com/agher') && html.includes('https://creativecommons.org/licenses/by/4.0/'), '样片必须保留作者和许可署名');
console.log('AH 原模型完整性、来源署名与样片导航检查通过。');
