# 官网真实产品演示记录

验证日期：2026-10-20（Asia/Shanghai）。

本记录来自当前 QRClaw 源码与实际模型调用。星河项目是专门编写的公开虚构样例，不对应真实客户、用户或项目业绩。运行记录位于 `website/.local/`，该目录已通过其 `.gitignore` 排除运行数据。

## 运行与隔离

可复用启动脚本：`website/tools/serve_demo.py`。

```bash
# 在仓库根目录执行
uv run --no-sync python website/tools/serve_demo.py --self-check
uv run --no-sync python website/tools/serve_demo.py
```

- 真实 API 与随包 Web UI：`http://127.0.0.1:8876/`。
- 记忆界面：`http://127.0.0.1:8876/#/memory`。
- 数据目录：`website/.local/demo-workspace/`。
- Workspace：`website-demo`，根目录为上述演示目录，沿用默认 Agent 的非 Docker 策略。
- 当前服务进程 PID：`40719`；终端会话句柄：`66241`。这些是本次观测结果，重启后会变化。最新 PID 写在 `website/.local/demo-api.pid`。
- 启动日志：`website/.local/demo-api.log`。
- `/health` 和 `/` 实际返回 200，服务 PID 存活已核验。

脚本只在独立进程内关闭 `MCP_ENABLED`、清空 MCP 服务列表；未更改用户配置文件。工具只保留 `read_file`、`write_file`、`list_directory`、`write_wiki_page`、`read_wiki_page`、`list_wiki_pages`、`search_wiki`。实际为 7 个工具（文件 3 个、记忆 4 个）。文件调用前检查解析后的真实路径，拒绝越界路径和越界符号链接。Shell、网络、Skills、Agent 管理及子 Agent 工具都未提供给演示模型。

自检验证了路径、符号链接边界和实际工具集合。现有 8765 API 与 5173 Vite 服务未重启或修改。模型推理使用已有配置，凭据未读取到输出、复制或写入演示文件。演示使用远程模型服务，不能描述为“全程离线”。

## 实际任务与结果

### 1. 整理公开样例文件

会话：`website-notes-20261020`，标题：**整理笔记 · 留下经验**。

输入：

> 请读取 notes 目录下的三份星河项目笔记，整理成一份简洁的项目摘要：目标、已完成、下一步和待确认。把结果保存为 output/星河项目摘要.md，随后告诉我最重要的三点。不要补充笔记里没有的事实。

真实工具序列：

1. `list_directory`
2. `read_file` × 3
3. `write_file`

结果文件：`website/.local/demo-workspace/output/星河项目摘要.md`。

已核对文件确实存在且含“目标、已完成、下一步、待确认”；明确标注虚构项目，保留“分类准确率尚未测量”和“发布日未确定”的边界。模型实际最终回复确认文件路径并归纳了三点。

原始返回：`website/.local/task-result.json`。完整会话与执行事件：`website/.local/website-notes-20261020.json`。

### 2. 主动保存汇报偏好

同一会话追加输入：

> 请记住我的项目汇报偏好：中文，先给结论，再列“已完成、下一步、待确认”；没有验证的数据标记待确认。以后整理项目笔记沿用这个格式。

真实调用 `write_wiki_page`。`GET /v1/wiki/pages` 确认产生：

- 页面：`项目汇报格式偏好`
- 状态：`ready`
- 版本：`1`
- 真实用户来源：2 条，均指向 `website-notes-20261020`
- 来源状态：`valid`
- `legacy_baseline: false`

`GET /v1/wiki/pages/项目汇报格式偏好` 返回了格式正文、来源消息和来源版本。原始返回已保存到 `website/.local/memory-page.json`；聊天回复保存到 `website/.local/memory-result.json`。

已知表现：本次模型生成的 Wiki 正文含字面量 `\n`，没有真实换行；本记录没有通过修改产品或手工改写记忆隐藏该表现。页面内容与来源均是实际运行结果。不能把这个样例写成“所有记忆格式均验证正常”。

### 3. 在新会话复用

独立会话：`website-reuse-20261020`，标题：**新的对话 · 延续偏好**。

新会话未包含第一条会话历史或具体格式要求，只输入：

> 这是新的对话。请沿用我之前确认的汇报偏好，读取 notes 里的星河项目笔记，给我一份简洁的项目汇报。

实际工具序列以 `read_wiki_page(name="项目汇报格式偏好")` 开始，然后读取样例文件。结果采用中文、结论先行，并依次包含“已完成、下一步、待确认”。这证明本次样例确实通过 Wiki 正文跨会话复用了偏好。

第一次复用回复自行推断了“已推进过半”，还把已明确的输入范围列为待确认。随后真实提交纠正消息：

> 请修正两处：笔记没有整体完成比例，不要写“已推进过半”；本地文件输入且不接第三方账号已经明确，不应列为待确认。请重新给出一份简洁汇报，沿用记住的结构，只保留源笔记明确事实。

模型最终回复已移除无依据的比例，把已明确的本地文件输入和不接第三方账号放到范围说明。展示最终回复时，可说“实际运行后的汇报”，不可称为“无需复核即可准确输出”。

证据文件：

- `website/.local/reuse-result.json`：首次复用返回。
- `website/.local/reuse-session-before-correction.json`：纠正前完整会话。
- `website/.local/reuse-corrected-result.json`：纠正后的真实返回。
- `website/.local/website-reuse-20261020.json`：最终会话与工具事件。

## 安装步骤核对

没有更改当前 `.venv`。在新的隔离虚拟环境中执行：

```bash
UV_PROJECT_ENVIRONMENT=website/.local/install-venv \
  uv sync --frozen --extra dev --no-editable
website/.local/install-venv/bin/qrclaw --help
website/.local/install-venv/bin/qrclaw serve --help
```

结果：从当前源码构建并安装 QRClaw `0.9.2`，127 个依赖安装成功；两个 help 命令退出码均为 0。`python -I` 核对导入位置在新环境的 `site-packages/qrclaw/__init__.py`，不是从工作目录直接导入源码。已有开发环境的 `uv run --no-sync qrclaw --help` 和 `serve --help` 也通过。

这是同一台 macOS 上的新虚拟环境验证；机器已有 Python 3.11.15、uv、可用网络和模型配置。未验证新装操作系统、Windows/Linux，也未重新走首次模型凭据配置；不能将其宣传为免配置即可使用。

## 可用于官网的证据边界

- 可以：真实工作台界面、文件读取与写入、实际工具事件、主动记忆、有效来源、新会话读取记忆、原生记忆管理页面。
- 本次没有执行：Plan 自动规划、子 Agent 协作、MCP 外部工具。它们的能力说明可引用源码，不能配上声称来自本次运行的伪造执行记录。
- 没有发送外部消息、修改私人资料、启动 MCP 服务、重连 VPN 或提交 Git。
- 浏览器截图与录屏由官网主任务执行；本记录不声称已经完成视觉验证或录制。

## GitHub 默认分支与演示版本核对

2026-10-20 只读执行：

```bash
git ls-remote --symref https://github.com/xiaofu2000/qrclaw.git \
  HEAD refs/heads/main refs/heads/dev-webui
```

结果：

| 对象 | 当前值 |
| --- | --- |
| GitHub 默认分支 | `main` |
| GitHub `main` | `2d3de6d365fd1139c978724a3cbced4a8adfc9c2` |
| GitHub `dev-webui` | `e1e261ed650f023c21af93283e4b359c62efbbd5` |
| 本地分支 | `dev-webui` |
| 本地 HEAD | `e1e261ed650f023c21af93283e4b359c62efbbd5` |

远端 `dev-webui` 与本次实际演示的产品源码 HEAD 完全相同。通过已存在的本地 Git 对象核对，`main` 是该提交的祖先，落后 7 个提交，差异包含：

- `5b33657`：Wiki 来源追溯、网页管理和并发恢复。
- `70191f1`：MCP 工具按需发现与能力目录搜索。
- 后续 5 个提交：工作台布局、小精灵及其状态和未读行为。

进一步通过 `git ls-tree` 确认 `main` 中没有 `qrclaw/server/wiki_routes.py` 和 `web/src/features/memory/MemoryPanel.tsx`。因此普通 `git clone` 默认得到的 `main` **不能覆盖官网当前展示的全部 UI 和记忆管理能力**。官网安装入口应明确展示开发分支，且注明“当前演示对应 dev-webui”，不能暗示这些改动已合并到默认分支或正式发布版本。

只读证据保存于 `website/.local/remote-refs.json`；未切换分支、fetch、merge 或 push。

## 首次空配置启动核对

进一步使用两个新目录模拟首次配置。测试仅在独立 Python 进程中把 `config_manager.CONFIG_DIR`、`CONFIG_FILE` 和工作空间根目录指向 `website/.local/`，没有修改 `$HOME` 或用户的真实配置。清除了该子进程继承的模型 API 环境变量，使用项目自带的默认空 Key 配置。

实际结果：

1. 调用 CLI `--help` 入口前配置不存在；入口自动生成配置，正常打印 help。默认 `api_key` 为空，默认 `model` 为 `gpt-4o`。
2. 正常 CLI 启动入口使用同样的空 Key 默认配置，可以完成 Provider、工具和会话初始化，出现输入提示。测试的输入函数只返回 `exit`，随后正常退出。
3. **没有复现“空 Key 导致客户端一启动便初始化失败”。** 当前 `LiteLLMProvider.__init__` 只保存配置；没有在构造时发起模型推理。
4. 本次没有对空 Key 发送模型请求；“能打开 CLI”不等于“已配置好可执行任务”。此前演示成功依赖本机已有的有效模型配置。

结果文件：

- `website/.local/first-run-help-check/result.json`
- `website/.local/first-run-check/result.json`

建议官网把安装说明拆成获取代码、生成并编辑模型配置、启动三步：

```bash
git clone --branch dev-webui https://github.com/xiaofu2000/qrclaw.git
cd qrclaw
uv sync
uv run qrclaw --help
```

然后明确提示：编辑 `~/.qrclaw/config.yaml` 中的 `llm.api_key`、`llm.model`，以及服务商要求时的 `llm.base_url`；完成后启动：

```bash
uv run qrclaw serve
```

浏览器打开 `http://127.0.0.1:8765`。`--help` 会在首次运行时生成配置并退出，比让首次用户先进入交互 CLI 再退出编辑配置更直接。配置文件已存在时初始化不会覆盖它。官网不需要承诺免费模型、免 Key 或无需配置。
