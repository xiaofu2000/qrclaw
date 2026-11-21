/** 官网演示数据与产品文案；示意内容不冒充实时 Agent 任务。 */
export const phases = [
  '向下滚动，展开一次任务的工作原理。',
  '明确目标，按需规划，让独立的子任务分开推进。',
  '调用工具完成工作，每个过程和结果都可以查看。',
  '把值得复用的信息沉淀为有来源的 Wiki 记忆。',
];

export const scenes = [
  { title: '交代任务', image: 'assets/product-task.webp', alt: '真实 QRClaw 工作台：用户要求整理三份示例项目笔记',
    caption: '读取三份示例项目笔记，整理目标、进度与待确认事项。' },
  { title: '查看执行', image: 'assets/product-process.webp', alt: '真实 QRClaw 工作台：展开工具调用与执行记录',
    caption: '实际调用文件工具读取笔记，将结果写入项目摘要文件。' },
  { title: '获得交付', image: 'assets/product-result.webp', alt: '真实 QRClaw 工作台：完成摘要并说明关键结果',
    caption: '查看已生成的摘要，继续对话，并明确需要保留的汇报偏好。' },
];

export const memories = [
  { slug: '输出偏好', title: '先给结论，再讲依据。', copy: '你偏好简洁的中文摘要：先说明结论，再列出依据与待办。后续任务可以检索这条偏好，减少重复交代。',
    source: '关联到提出这项偏好的原始对话', tags: ['用户偏好', '输出格式'] },
  { slug: '项目背景', title: '新的对话，已有上下文。', copy: '记录项目的目标、边界与已确认的约定。下次讨论具体工作时，可以先查阅背景，再继续推进。',
    source: '关联到确认项目目标与范围的对话', tags: ['项目知识', '范围约定'] },
  { slug: '技术决策', title: '记住选择，也记住原因。', copy: '保存技术选择和当时的依据。约定变化时可以更新记忆，并查看来源，避免把旧方案误当成现状。',
    source: '关联到讨论并确认技术方案的对话', tags: ['技术决策', '可追溯'] },
];

export const entries = {
  web: { title: '把工作过程展开来看。', copy: '会话、执行过程和记忆管理在同一工作台。启动本地服务，然后打开浏览器。',
    file: '启动 Web 工作台', code: 'uv run qrclaw serve\n\n# 浏览器打开\n# http://127.0.0.1:8765' },
  cli: { title: '在终端，直接开始对话。', copy: '使用交互式 CLI 交代任务，通过 /agent、/session、/skill 管理工作资源。',
    file: '启动命令行', code: 'uv run qrclaw\n\n# 在交互界面管理资源\n/agent\n/session\n/skill' },
  api: { title: '把 Agent 接入你的应用。', copy: '本机服务提供 OpenAI 兼容接口。以下示例会发起真实请求；请先完成本地模型配置。',
    file: 'OpenAI 兼容 API', code: `curl http://127.0.0.1:8765/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "qrclaw-agent",
    "messages": [
      {"role": "user", "content": "你好，介绍一下你能做什么"}
    ]
  }'` },
};
