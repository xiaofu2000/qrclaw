---
name: QRClaw
description: 工作项——深浅两套主题、聊天为主体、属性栏常驻的日常 AI 工作台
colors:
  dark:
    canvas: "#0a0a0b"
    surface: "#0f0f11"
    surface-raised: "#16161a"
    surface-soft: "#1c1c21"
    ink: "#e6e6e8"
    ink-2: "#c4c4ca"
    muted: "#8e8e96"
    faint: "#86868e"
    line: "#232327"
    line-strong: "#2e2e34"
    accent: "#6d8bff"
    accent-soft: "#1d2445"
    user-bubble: "#4a63e0"
    danger: "#f2788a"
    success: "#4cb782"
    warning: "#e2a336"
  light:
    canvas: "#f5f5f7"
    surface: "#fcfcfd"
    surface-raised: "#fff"
    surface-soft: "#eeeef2"
    ink: "#18181b"
    ink-2: "#3a3a42"
    muted: "#5f5f68"
    faint: "#696972"
    line: "#e5e5ea"
    line-strong: "#d7d7de"
    accent: "#3d5ee8"
    accent-soft: "#e9edff"
    user-bubble: "#3d5ee8"
    danger: "#c4314b"
    success: "#23895a"
    warning: "#b06e0a"
typography:
  family: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
  mono: "'JetBrains Mono', ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace"
  base: "15px / 1.6"
  reply: "16px / 1.85"
  user-bubble: "15.5px / 1.75"
  session-title: "24px / 1.45, 600"
  label: "13px"
sizes:
  sidebar: "272px（≤1100px 为 240px）"
  properties: "340px，宽于 1100px 常驻，否则为抽屉"
  chat-column: "最大 840px"
  sidebar-sprite: "56px 圆角底板"
  chat-avatar: "36px（手机 30px）"
  composer: "默认一行，最高 180px 后内部滚动"
---

# Design System: QRClaw

## Overview

**北极星：「工作项」。** 每个会话都是一项持续几天的工作，有状态、有规模、有留下的经验。界面结构借鉴 Linear 的 issue 详情：左边是导航与会话，中间是对话，右边是属性。对话区按聊天软件的习惯组织，你和 QRClaw 的消息一眼就能分开。

这一版来自 2026-11-06 的三方向评审，用户在 iA「工作文档」、TE「仪器」、Linear「工作项」三个方向里选了「工作项」，并在原型阶段提出四条调整：同时要深色和浅色、头像和品牌图标要大、输入框默认一行、用户与 Agent 消息要明确区分。原型与评审记录在 `design-demos/`。

**关键特征：**
- 深浅两套主题共用一套变量名，默认跟随系统，侧栏底部可切换为浅色或深色，选择存在浏览器 `qrclaw.theme`。
- 面板之间只用 1px 线分隔，不用投影堆层级；只有窄屏抽屉有阴影。
- 小精灵是唯一的动画实例：桌面在侧栏顶部，手机在顶栏。聊天和属性栏里的 QRClaw 头像是同一引擎渲染的静态帧，不额外挂载动画。

## Colors

两套主题的取值见 frontmatter，组件只引用变量名，不写死颜色。

- **accent**：链接、当前选择、运行中的状态、焦点框。
- **user-bubble**：你的消息气泡与发送按钮，配白字，两套主题下对比度都在 4.5:1 以上。
- **surface-raised**：QRClaw 回复卡片、输入框、浅色下的白卡。
- **success / warning / danger**：完成、整理阈值、错误；状态始终附文字。

记忆页（`features/memory/memory.css`）同样只引用这些变量，跟随主题切换。

## Typography

一套无衬线字体栈，中文走系统字体；数字、工具名、代码用等宽。正文 15px，回复正文 16px、行高 1.85；会话标题 24px；辅助信息 13px，最小 12px。

## Layout

- **侧栏**：顶部 56px 小精灵底板加名称和实时状态；下面依次是「开启新对话」、导航（收件箱、当前对话、记忆库）、会话列表、底部的本地工作空间与主题切换。会话前的状态图标：转圈表示运行中，实心环表示有未读回复，虚线圈表示空白，对勾表示已读。
- **对话区**：顶部是会话标题、消息数与上下文整理次数，以及「全部 / 只看对话 / 只看过程」三个筛选。每条你的消息开启一轮，右对齐蓝色气泡，带 36px「你」头像；到下一条你的消息之前，QRClaw 的回复、工具调用、子 Agent 和执行汇报都收进左侧同一条消息里，带 36px 小精灵头像，并保持原始顺序。连续的工具调用合并成一行，显示次数、工具名和失败次数，展开后每次调用一行：工具名、参数摘要（命令、路径、搜索词等），点开看结果；写入记忆的调用另起一行，列出实际写入的页面名，点击直接打开那一页（`#/memory/页面名`），旧会话没有页面名时提供记忆库入口。复制按钮只放在这一轮最后一段回复下。
- **输入区**：默认一行，发送按钮同排，随内容长高到 180px 后内部滚动；Plan、上下文占比和按键提示在框外的工具行。中文输入法确认不触发发送。
- **属性栏**（`features/inspector/InspectorPanel.tsx`）：状态、执行者、Plan、上下文（含整理阈值刻度）、已整理次数、累计用量、工具调用统计、子 Agent 列表、写入记忆次数、最近记录。数字全部从会话事件推导（`features/chat/chatModel.ts` 的 `sessionStats`，聊天区与属性栏共用），写入记忆一项列出本会话写入的页面名。点选子 Agent 后，同一面板显示它的任务、完整会话和工具状态。宽于 1100px 时常驻，否则从顶栏「属性」按钮打开抽屉。
- **收件箱**（`#/inbox`）：会话按运行中、等你查看、其他分组，显示消息数与更新时间。每个会话的工具次数需要会话列表接口新增字段，目前不显示。会话标题在首轮完成后由服务端自动生成（见 README）。
- **记忆库**（`#/memory`）：结构不变，沿用列表、正文、来源与关联的布局，换成主题变量。

响应式：≤1100px 属性栏改为抽屉、侧栏 240px；≤760px 侧栏改为左侧抽屉，顶栏放小精灵与状态，消息头像缩为 30px，你的气泡最多占 84% 宽度。

## Elevation & Depth

默认不用阴影。卡片靠 1px 边线与 surface-raised 区分；窄屏的侧栏与属性抽屉使用阴影与遮罩。
