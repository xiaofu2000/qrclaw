/* 三个方向共用的演示数据。
   只摘取页面会显示的片段，全部来自本机 QRClaw 会话 20261012-0fbd13e0 和 Wiki（2026-11-06 读取），
   没有编造的数字。工具顺序 seq 由事件日志按「工具开始」逐条取出：
   s=run_shell r=read_file/grep_code k=read_wiki_page w=write_wiki_page q=web_search/web_extract
   m=MCP（call_tool/codegraph_explore/search_tools） A=启动子 Agent |=上下文压缩完成 */
window.QR = {
  session: { title: '你为什么不用codegraph mcp呢 你记住，能用codegraph就用codegraph，', messages: 99, tools: 107,
    subagents: 4, tokens: 6089838, ctx: 144571, limit: 485000, threshold: 436500, updated: '10月16日 17:13', pct: 29.8 },
  sessions: [
    { id: 's1', title: '你为什么不用codegraph mcp呢 你记住，能用codegraph就用codegraph，', updated: '10月16日 17:13', messages: 99, tools: 107, subagents: 4, unread: true, pct: 29.8 },
    { id: 's2', title: '看看qrclaw项目的记忆系统与codex的有什么区别 /Users/admin/Documen', updated: '10月13日 12:37', messages: 298, tools: 155, subagents: 0, unread: true, pct: 73.4 },
    { id: 's3', title: '帮我看一下我的 /Users/admin/AndroidStudioProjects/ai_re', updated: '10月12日 15:40', messages: 8, tools: 2, subagents: 7, unread: true, pct: 5.0, plan: true },
    { id: 's4', title: '新对话', updated: '10月12日 19:37', messages: 0, tools: 0, subagents: 0, unread: false, pct: 0 }
  ],
  toolTotals: [['run_shell', 65], ['read_file', 16], ['web_search', 5], ['write_wiki_page', 5], ['mcp__call_tool', 5],
    ['read_wiki_page', 3], ['codegraph_explore', 3], ['grep_code', 2], ['web_extract', 2], ['search_tools', 1]],
  seq: 'AAAArssssrskkrrrrrmsqsssssqss|ssssssssssssssssssssssssrrrsssssssrssss|wmssssssmmmsmrmrrmrwksqqqsssswsqqsssrmrrsww',
  subagents: [
    { id: 'step-1', task: '通过网络搜索调研 codegraph 的 MCP 服务：它是什么、官方仓库/文档地址、安装方式', state: '已完成' },
    { id: 'step-2', task: '探测当前运行环境中 MCP 配置文件的存放位置，查找常见的 MCP 客户端配置文件', state: '已完成' },
    { id: 'step-1', task: '读取 ~/.qrclaw/config.yaml 的完整内容，重点检查 mcp.servers 字段当前是否为空', state: '已完成' },
    { id: 'step-1', task: '用 Python 脚本读取 config.yaml，定位 mcp.servers 中 label 为 codegraph 的条目', state: '已完成' }
  ],
  compressions: [{ b: 78572, a: 15987 }, { b: 84557, a: 23143 }],
  prev: {
    user: '我感觉我需要装吗 什么意思没明白',

    reply: '别急，我说人话。\n\n**skill 不是"功能"，是一份写给 AI 看的"使用说明书"。**\n\n- **MCP（你已经装好的）= 一整套工具**。等于给 AI 配了锤子、螺丝刀、显微镜，让它能真的去拆开一个软件看内部。\n- **skill = 一本操作手册**。里面写着"遇到这类活儿，先检查环境、再选引擎、然后按这个顺序拆"。\n\n那我建议你**现在不用装**：你平时问的都是**自己项目的代码**，这种用 **codegraph** 就够了。'
  },
  turn: {
    user: 'npx skills add morluto/rea --skill reverse-engineer-anything\n现在的qrclaw没适配这样的功能是吧  你先不要改代码',
    at: '10月16日 16:33',
    duration: '4 分 46 秒',
    tools: [
      { t: 'run_shell', at: '16:33:21', d: '1.1秒' }, { t: 'grep_code', at: '16:33:22', d: '0.1秒' },
      { t: 'mcp__call_tool', at: '16:33:29', d: '0.2秒' }, { t: 'read_file', at: '16:33:37', d: '0.1秒' },
      { t: 'read_file', at: '16:33:37', d: '0.1秒' }, { t: 'run_shell', at: '16:33:45', d: '4.0秒' },
      { t: 'write_wiki_page', at: '16:34:00', d: '2分43秒', page: 'QRClaw Skill 机制' },
      { t: 'write_wiki_page', at: '16:36:48', d: '1分19秒', page: '代码分析工具偏好' }
    ],
    wrote: ['QRClaw Skill 机制', '代码分析工具偏好'],
    reply: '对，**没适配**——而且比你以为的还少一截。\n\n分三点说：\n\n**1. QRClaw 其实自带一套 skill 系统**\n格式是 `SKILL.md` + YAML frontmatter，代码注释原话"完全兼容 OpenClaw 的 SKILL.md 格式"。也就是说它跟 Claude Code / Codex / skills.sh 的 skill **是同一个格式**。\n它加载的地方是：`~/.qrclaw/agents/default/skills/<技能名>/SKILL.md`\n→ **这个目录现在是空的，0 个技能**。\n\n**2. 它不认识 `npx skills add`**\n那条命令是 skills.sh 的 CLI，只往它认识的 client 目录写——Claude Code / Codex / Cursor。**它名单里没有 QRClaw**，所以跑它不会落到 QRClaw 的目录里。\n\n**3. QRClaw 自己的入口 `/skill import` 是空壳**\n它有 `/skill list` 和 `/skill import <名字|url>`。但 `import` 的代码只是**打印一句话**：\n> "请在对话中说：导入 skill X，LLM 会用 install-skill skill 帮你下载"\n\n而 `install-skill` 这个技能**代码里根本没实现**。所以现在你敲 `/skill import` **不会真的下载任何东西**。\n\n**结论**：\n- 想让 QRClaw 用上 REA 的这个 skill，**当前只能手动**：把那个 `SKILL.md` 目录整个拷进 `~/.qrclaw/agents/default/skills/`。\n- 想让它像 `npx skills add` 一样一键装，**得改代码**（你说先不改，那就不动）。\n\n要不要我帮你**手动拷一份**到 QRClaw 的 skills 目录试试（只拷文件、不改代码）？还是先算了。'
  },
  memoryPending: 1,
  memory: [
    { name: 'QRClaw Skill 机制', desc: 'QRClaw 自带 Skill 系统：格式、加载路径、use_skill 调用链、现状与安装缺口。', tags: ['QRClaw', 'Skill', '扩展机制'], updated: '10月16日 16:41', status: 'ready', sources: 2 },
    { name: 'REA 使用方式', desc: 'REA 使用方式：MCP/CLI、安装（含 skill-only）、依赖、证据、本机可用性及 QRClaw 接入边界。', tags: ['REA', '逆向工程', '工具配置'], updated: '10月16日 16:41', status: 'ready', sources: 8 },
    { name: '代码分析工具偏好', desc: '偏好用 codegraph、REA 分析代码，优先 codegraph_explore 替代逐文件 grep。', tags: ['工具偏好', '代码分析', 'codegraph'], updated: '10月16日 16:38', status: 'ready', sources: 5 },
    { name: 'ai_remind 登录失效停语音', desc: 'ai_remind 登录失效/退出登录时停止语音会话的触发源、双路径、关键组件、脆弱点与测试状态。', tags: ['ai_remind', '语音会话'], updated: '10月14日 16:01', status: 'ready', sources: 4 },
    { name: 'Claude Code 记忆与上下文压缩', desc: 'Claude Code 记忆/压缩：双轨记忆、三档压缩、REA 证据边界与 app.asar 配置面限制。', tags: ['Claude Code', '记忆系统'], updated: '10月13日 12:41', status: 'ready', sources: 7 },
    { name: 'REA MCP 接入', desc: 'REA MCP 接入 QRClaw：配置、白名单、CLI 与 JS 分析边界。', tags: ['REA', 'MCP', 'QRClaw'], updated: '10月13日 12:40', status: 'ready', sources: 10 },
    { name: 'LLM Wiki 记忆模式', desc: 'LLM Wiki 记忆模式：Karpathy 出处、QRClaw 落地、与 Codex CLI 对比及暂缓的改进方案。', tags: ['记忆系统', 'LLM Wiki'], updated: '10月13日 12:37', status: 'ready', sources: 2 },
    { name: 'QRClaw', desc: 'QRClaw 本地自主 AI Agent 的能力、可承担工作与记忆系统方向及实现细节', tags: ['AI Agent', 'QRClaw'], updated: '10月12日 21:08', status: 'legacy', sources: 0 },
    { name: 'codegraph MCP 接入', desc: 'codegraph MCP 接入指南：安装配置、QRClaw 接入、使用约定与实测记录。', tags: ['MCP', 'codegraph'], updated: '10月12日 20:52', status: 'legacy', sources: 0 },
    { name: 'ai_remind 多轮语音 ViewModel 重构方案', desc: '多轮语音 ViewModel 重构方案，含阶段2落地、测试结果与后续抽离计划。', tags: ['ai_remind', '重构'], updated: '10月12日 20:52', status: 'legacy', sources: 0 },
    { name: 'Codex CLI 记忆系统', desc: '从落盘产物反推 Codex CLI memories：存储布局、map-reduce 流水线、SQLite 结构。', tags: ['Codex', '记忆系统'], updated: '10月12日 20:51', status: 'legacy', sources: 0 },
    { name: 'ai_remind 多轮语音会话', desc: 'ai_remind 多轮语音会话：协议、状态机、协调器、落库、边界与测试。', tags: ['语音会话', 'ai_remind'], updated: '10月12日 20:37', status: 'legacy', sources: 0 },
    { name: 'ai_remind 单轮语音链路', desc: 'ai_remind 中长按一句话单轮语音链路的实现与与多轮的差异。', tags: ['语音输入', 'ai_remind'], updated: '10月12日 15:39', status: 'legacy', sources: 0 },
    { name: 'ai_remind 数据同步流程', desc: 'ai_remind 的数据同步机制：三层同步链路、拉取/上传、冲突合并策略、触发时机。', tags: ['ai_remind', '数据同步'], updated: '10月12日 14:59', status: 'legacy', sources: 0 },
    { name: 'ai_remind 项目架构', desc: 'ai_remind Flutter 项目的技术栈、目录结构与模块概览。', tags: ['Flutter', '架构'], updated: '10月12日 14:30', status: 'legacy', sources: 0 }
  ],
  page: {
    name: 'QRClaw Skill 机制', revision: 2, links: ['QRClaw', 'REA 使用方式', 'REA MCP 接入'], backlinks: ['REA 使用方式'],
    sources: [{ role: '用户消息', at: '10月16日 16:33' }, { role: '助手背景', at: '10月16日 16:38' }],
    content: '## 格式与兼容性\n- 格式：`SKILL.md + YAML frontmatter`，代码注释称"完全兼容 OpenClaw 的 SKILL.md 格式"（`qrclaw/skills/registry.py`）。\n- 与 Anthropic / Claude Code / Codex / skills.sh 生态的 skill 是同一格式。\n\n## 加载路径\n- 默认加载路径为 `~/.qrclaw/agents/default/skills/<技能名>/SKILL.md`。\n- 顶层 `~/.qrclaw/skills` 不是 agent 的 skills 目录，也不是读取位置。\n- `SkillRegistry` 是单例，`load_from_dir` 只扫描一次，并按目录缓存。\n\n## 调用链\n- `use_skill` → `get_skill_registry()` → `load_from_dir` → `Skill.from_skill_md`。\n- `use_skill` 注入技能完整信息 + 执行指导，是"按需加载"，不是把技能注册成工具。'
  }
}

/* 极简 Markdown：段落、加粗、行内代码、列表、引用、二级标题。仅用于原型展示。 */
window.md = function (src) {
  const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const inline = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  return src.split(/\n{2,}/).map(block => {
    const lines = block.split('\n')
    if (lines.every(l => l.startsWith('- '))) return '<ul>' + lines.map(l => '<li>' + inline(l.slice(2)) + '</li>').join('') + '</ul>'
    if (lines[0].startsWith('## ')) return '<h3>' + inline(lines[0].slice(3)) + '</h3>' + (lines.length > 1 ? window.md(lines.slice(1).join('\n')) : '')
    if (lines.every(l => l.startsWith('> '))) return '<blockquote>' + inline(lines.map(l => l.slice(2)).join(' ')) + '</blockquote>'
    const head = lines.findIndex(l => l.startsWith('- '))
    if (head > 0) return '<p>' + lines.slice(0, head).map(inline).join('<br>') + '</p>' + window.md(lines.slice(head).join('\n'))
    return '<p>' + lines.map(l => l.startsWith('> ') ? '</p><blockquote>' + inline(l.slice(2)) + '</blockquote><p>' : inline(l)).join('<br>').replace(/<br><\/p>/g, '</p>').replace(/<p><br>/g, '<p>') + '</p>'
  }).join('')
}
