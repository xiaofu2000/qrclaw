# Ponytail 项目清理记录

日期：2026-09-15

## 结果

使用 Ponytail 审计流程和 Codegraph 调用关系，扫描 Python 后端、工具、记忆、MCP、沙箱、执行服务以及 Web 客户端；对发现的问题核对源码并修复。

- 删除 14 个冗余文件；生产代码新增 184 行、删除 2,127 行，净减少 **1,943 行**。统计不含测试、文档和锁文件，也不含原有未提交的测试改动。
- 删除未使用的直接依赖 `google-genai`，锁文件同步移除其独占依赖 `websockets`；其余依赖版本未变。
- 保留客户端现有 MVVM 分层，事件修复位于 Repository、Service 和投影层。
- 保留工作开始前的测试、前端测试配置及分析文档改动；未创建 Git 提交。

## 删除和复用

| 类型 | 清理内容 | 当前实现 |
|---|---|---|
| delete | 旧 `memory/storage`、`memory/types` 体系，以及仅做转发的 Wiki 管理器 | `WikiMemory`、`WikiPage`、`IndexManager` |
| delete | 旧记忆导入兼容文件、错误的 `ContextManager = None` 导出 | 从实际实现模块导入 |
| delete | 重复 Wiki 选择器和没有消费者的提取状态索引 | `memory/wiki/selection`、现有提取节点 |
| delete | 未调用的整套异步网页抓取链路 | 现有同步多后端抓取链路 |
| delete | 重复助手消息序列化、被同名包遮蔽的 `cli.py`、空策略占位目录 | `message_codec`、`cli` 包 |
| shrink | 单渠道动态加载注册表、未使用导入、无效参数、未使用提示构造 | 直接构造现有 Provider，保留必要工具注册副作用 |
| stdlib | 自定义空上下文管理器 | `contextlib.nullcontext` |
| shrink | 手写 Wiki 响应字段拼装及不完整校验 | 现有 Pydantic Schema 校验 |

## 修复

1. 默认 Wiki 查询引用不存在的模块，并读取错误的上下文字段；改为当前线程工作区。
2. 清空 Wiki 上下文、结束或替换计划后，系统提示缓存仍保留旧内容；统一使缓存失效。
3. 压缩超预算时丢弃尚未纳入摘要的消息；保留这些消息，拒绝空摘要并刷新 token 估算。
4. 相同会话 ID 切换日志目录或配置时被错误跳过；重新应用日志配置。
5. SQLite 上下文只提交或回滚事务而不关闭连接；通过统一上下文管理器确保关闭。
6. Wiki 页面名可跨目录读写，符号链接可指向库外；在页面访问公共入口校验。
7. 删除强制关闭 TLS 证书校验的设置，恢复库的默认校验行为。
8. WebSocket 旧连接延迟回调、重复清理可能影响新任务；隔离旧订阅并释放回调。
9. 快照恢复期间的新事件被丢弃；串行归并事件，避免旧运行快照覆盖当前运行。
10. 无效 WebSocket 消息导致未处理异常；显示中文错误并允许后续正常事件继续处理。
11. 完成事件没有修正部分流式正文；前后端均使用完成事件携带的完整正文。
12. 部分 Wiki 测试依赖真实模型、使用固定临时目录且缺少断言；改为离线响应和隔离目录，并收紧 Provider 异常断言。

## 旧接口迁移

本次移除了没有仓库内消费者的旧导入入口。对应实现路径：

- `qrclaw.memory.session` → `qrclaw.memory.context.session`
- `qrclaw.memory.step_result` → `qrclaw.memory.context.step_result`
- `WikiMemoryManager` / `LongTermMemory` → `qrclaw.memory.wiki.WikiMemory`
- `qrclaw.memory.wiki.selector` → `qrclaw.memory.wiki.selection`
- `qrclaw.llm.chat` → `get_llm_service().chat(...)`，正文取返回对象的 `content`
- `ContextManager` → `qrclaw.memory.context.context_manager.ContextManager`

## 验证

- 后端：`python -m pytest qrclaw/tests qrclaw/sandbox/tests qrclaw/memory/wiki/selection/test_selector.py -q`，**160 passed**。
- 前端：`npm test`，**43 passed**。
- 前端 `npm run lint`、`npm run build`、`npm run test:typecheck` 均通过。
- Python Ruff F 规则扫描、`compileall`、CLI `--help` 冒烟检查均通过。
- `uv lock --check --offline`、`git diff --check` 均通过。
- 后端仍有一项第三方 Starlette/httpx 弃用提示，未通过升级依赖扩大改动。

验证覆盖本地逻辑与模拟外部服务；未执行真实模型、远端 MCP、网页抓取或 Docker 容器集成验证。此次审计结果是已确认问题的清理与修复，不代表形式化证明项目不存在其他缺陷。
