import { cp, rm } from 'node:fs/promises';

/** 将已构建的站点复制到托管支持的输出目录；dist 只存放构建产物。 */
const output = new URL('../dist/', import.meta.url);
await rm(output, { recursive: true, force: true });
await cp(new URL('../site/', import.meta.url), output, { recursive: true });
console.log('静态发布目录 dist 已生成。');
