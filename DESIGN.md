---
name: QRClaw
description: 蓝图工作室——明亮、清晰、由真实任务状态驱动的日常 AI 工作台
colors:
  accent: "#315cda"
  accent-hover: "#254ab9"
  accent-soft: "#eaf0ff"
  canvas: "#edf2f8"
  surface: "#fff"
  surface-soft: "#f5f7fb"
  ink: "#202c40"
  muted: "#5d6b80"
  line: "#dce3ed"
  danger: "#b33842"
  success: "#28725c"
  warning: "#946614"
typography:
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
    fontSize: "32px"
    fontWeight: 650
    lineHeight: 1.35
    letterSpacing: "-0.03em"
  page-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
    fontSize: "22px"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.025em"
  document-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
    fontSize: "24px"
    fontWeight: 650
    lineHeight: 1.45
    letterSpacing: "-0.025em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.85
  reading:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.85
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  compact: "6px"
  control: "8px"
  list: "9px"
  action: "10px"
  card: "12px"
  radius: "14px"
  composer: "16px"
  work-surface: "18px"
spacing:
  "4": "4px"
  "8": "8px"
  "12": "12px"
  "16": "16px"
  "20": "20px"
  "24": "24px"
  "28": "28px"
  "32": "32px"
  "36": "36px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.action}"
    width: "100%"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "7px 11px"
  button-icon:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    rounded: "{rounded.control}"
    width: "36px"
    height: "36px"
  button-send:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.action}"
    width: "36px"
    height: "36px"
  input-search:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.list}"
    padding: "0 11px"
  navigation-item:
    backgroundColor: "transparent"
    textColor: "#4e607a"
    rounded: "{rounded.list}"
    padding: "10px 12px"
  plan-switch:
    backgroundColor: "{colors.surface-soft}"
    textColor: "{colors.muted}"
    rounded: "{rounded.compact}"
    padding: "6px 8px"
  subagent-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.accent}"
    rounded: "{rounded.card}"
    padding: "15px 16px"
    width: "100%"
  composer:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.composer}"
    padding: "16px"
---

# Design System: QRClaw

## Overview

**Creative North Star: "蓝图工作室"**

蓝灰色桌面托起明亮的白色工作面，黑色 bloub 伙伴提供持续可见的陪伴与任务状态。界面的个性来自清楚的区域关系、准确的状态反馈和轻盈的留白；主要内容仍是用户输入、回复与记忆正文。

操作区保持紧凑，阅读区保留宽松行距。钴蓝集中标识主要动作、当前位置、可访问的链接与正在进行的工作；轻边线和中性色阶组织信息，柔和阴影仅区分工作面与抽屉的空间层级。

**Key Characteristics:**
- 蓝灰底、白色工作面、钴蓝动作色。
- 同一套中文系统字体，标题、正文与辅助信息层次分明。
- 黑色伙伴与文字状态共同反馈真实任务阶段，工作时融入侧栏或顶栏。
- 独立滚动的工作区域，窄屏通过抽屉与列表、详情切换保留阅读空间。

本文件依据当前 `web/src/styles/global.css`、`web/src/features/memory/memory.css` 与组件代码提取；已有桌面、手机截图仅用于核对组成。frontmatter 是已实现值的规范记录，尚未改写为 CSS 变量的间距和圆角仍是提取尺度。未使用的 `--sidebar` 不纳入色板。色阶预览属于 sidecar 展示数据，不是新增界面配色。

## Colors

冷调纸面与蓝灰文字建立长时间工作的安静背景，钴蓝提供明确的交互重心。

### Primary
- **工作钴蓝**（`accent`）：新对话、发送、当前 Plan 状态、链接与进行中的任务。
- **深钴蓝**（`accent-hover`）：新对话与发送按钮悬停。记忆保存按钮现有悬停值为 `#254ab8`，保留为该组件局部差异，不冒充统一 token。
- **浅钴蓝**（`accent-soft`）：轻操作悬停、Plan 开启及记忆条目选中。主导航当前项另有局部背景和文字值，组件示例按源代码保留。

### Neutral
- **蓝灰桌面**（`canvas`）：外层空间与侧边导航的底色。
- **白纸**（`surface`）：工作面、输入区与默认按钮。
- **雾白**（`surface-soft`）：过程记录、代码相关容器、辅助控件与悬停面。
- **墨蓝**（`ink`）：正文与主要标题。
- **灰蓝**（`muted`）：说明、辅助标签与输入提示。placeholder 与普通辅助文字使用相同的可读颜色。
- **细线蓝灰**（`line`）：分区边线、表格与默认描边。

状态色使用 **完成绿**（`success`）、**错误红**（`danger`）、**等待赭黄**（`warning`），分别对应成功、失败、等待或处理中；状态始终附有文字。它们是功能色，不作为第二套装饰主题。

**The Action Blue Rule.** 钴蓝对应动作、选择、链接或真实运行状态；大面积阅读背景使用中性色。

## Typography

**Body Font:** frontmatter 中的中文系统无衬线字体栈，按平台回退。操作标题共用该字体栈；当前没有独立品牌 display 字体，不将其推定为展示页的字体规范。

**Character:** 中文优先，靠大小、字重与行距表达层级。代码块保留浏览器的等宽字体回退，记忆来源的原文块则明确继承界面字体。

### Hierarchy
- **Headline**：欢迎标题使用 `headline`；中等窗口缩为 28px，手机为 27px。
- **Page title**：记忆页标题使用 `page-title`；手机为 20px。
- **Document title**：记忆文档标题使用 `document-title`；手机为 22px。
- **Body**：聊天内容使用 `body`；手机字号缩为 14px，保持行距。Markdown 一级至三级标题分别为 25px、21px、18px。
- **Reading**：记忆正文使用 `reading`，最大行宽为 75ch。
- **Label**：记忆操作按钮采用 `label`；导航和常规说明多为 13px，次要元信息为 10–12px。计数、时间和上下文用量使用等宽数字。

**The Reading Rhythm Rule.** 正文保持宽松行距，工具与元信息保持紧凑；不要用辅助标签的字号承载长回复。

## Layout

桌面外壳高为 100dvh，默认导航宽 240px，工作面上、右、下留 16px 外沿。顶部栏高 56px；工作面使用 flex/grid 的 `min-width: 0`、`min-height: 0` 将长内容留在所属滚动区。执行过程展开时占右侧 300px。已有会话与记忆页的伙伴放在导航底部，不再独占正文上方的状态带。

聊天消息与输入区的外部最大宽度均为 820px，桌面左右内边距 36px；欢迎伙伴行最大宽度为 740px。消息间隔 28px，用户消息最多占可用宽度的 88%，手机为 92%。输入区位于消息滚动区之外，并计入底部安全区。

记忆页以紧凑工具栏保留标题、刷新和检查入口。错误提示、任务与提取记录、检查结果统一置于工具栏之后、列表与详情之前的 `memory-auxiliary` 辅助区，总高度上限为 `min(240px, 30%)`，共享一个滚动区。任务与提取记录默认收起，检查结果首次出现时展开且可收起；各块不再独立限高和嵌套滚动，避免同时展开时累加挤占正文。

记忆页在宽度大于 1000px 时采用列表与详情两栏，列表条目与详情各自滚动。工作区与两栏外层使用 `overflow: clip`，避免焦点操作触发外层程序滚动。列表默认宽 280px，详情内边距为 20px 28px 32px；宽度不超过 1120px 时列表为 240px，详情内边距为 20px 24px，编辑表单属性改为单列。1500px 以上详情左右内边距扩为 52px，标题与操作可并排。

响应规则按现有边界保留，不将相近断点强行合并：
- **≤1100px**：主导航为 220px；执行过程转为右侧抽屉，宽 340px，保留视口外沿与遮罩。
- **≤1000px**：记忆列表与详情一次只显示一栏。选中记忆进入详情，通过“返回记忆列表”回到列表；返回只切换展示，保留搜索条件与编辑草稿，并恢复列表中当前条目的焦点。若当前条目不在筛选结果内，则聚焦搜索框；进入详情与返回列表均使用 `preventScroll`，保留各自滚动位置。
- **≤760px**：工作面铺满视口，无外沿、外层圆角或阴影；顶部栏高 64px，同时容纳工作伙伴。导航转为宽 292px 的左侧抽屉，受视口最大宽度约束。记忆页延续单栏切换，详情内边距为 12px 16px 28px，表单属性保持单列。
- **≤660px 高度**：欢迎区域从顶部开始排布，欢迎伙伴缩小，使输入可通过滚动访问；导航底部的工作伙伴也缩小。

间距以 frontmatter 的提取尺度为主，紧密组常用 4–12px，区块间常用 16–36px。现有组件也有 7px、18px、22px、26px 等局部值，未被统一改写。

## Elevation & Depth

以白底、浅色辅助面与细分隔线为主，仅外层工作面和浮动执行抽屉使用柔和阴影。普通按钮、消息、记忆条目与子任务卡片不依靠投影表达可点击性。

### Shadow Vocabulary
- **工作面悬浮**：`0 6px 30px #253b6010`，区分桌面底色与白纸工作区；手机撤去。
- **执行抽屉**：`-12px 0 40px #172c4720`，配合 `#152a4a44` 遮罩区分覆盖层。

**The Quiet Surface Rule.** 常规内容靠底色和细线分区；保留阴影给承载整个工作区的表面与覆盖式执行抽屉。

## Shapes

圆角按对象尺度分配：小型文字操作采用 `compact`，常规按钮采用 `control`，导航与输入字段采用 `list`，主要动作采用 `action`，子任务与提示容器采用 `card`，消息编辑容器采用 `radius`，大输入区与抽屉采用 `composer`，桌面工作面采用 `work-surface`。

圆角是角色相关的提取尺度，并非所有容器统一为 12–16px。用户消息采用 `14px 14px 4px 14px` 的方向性轮廓；状态点和开关滑块为圆形，回到底部按钮为 20px 圆角。图标使用现有 Lucide 描边 SVG，伙伴保留 bloub 原始有机路径与动画。

## Components

### Buttons
- **主要操作**：新对话为全宽钴蓝按钮，最小高 44px，13px、600 字重；发送为 36px 方形图标按钮，手机为 34px。
- **次要操作**：白底细描边，采用 `button-secondary`；记忆页主要保存操作沿用其 8px 圆角和紧凑尺寸，改变为钴蓝底。
- **轻操作**：图标按钮为 36px、透明底；悬停和展开时用浅钴蓝背景及钴蓝图标。
- **状态**：常规按钮禁用时降低不透明度；发送按钮有独立灰蓝禁用配色。按下使用 `brightness(.96)`，颜色状态切换为 160ms ease-out。焦点使用 2px 钴蓝轮廓，偏移 3px。

### Inputs / Fields
- **消息输入**：白底大圆角容器，1px `#c7d3e6` 边线；聚焦时边线变为钴蓝。textarea 桌面为 15px、手机为 16px，初始一行，随换行与文本折行增高至 180px，超过后内部滚动，清空后恢复一行。发送按钮与输入同排，Plan 和上下文位于下方紧凑工具行；提示文字为 `muted`。
- **搜索**：图标和输入位于同一 9px 圆角描边框内，桌面最小高 38px，手机为 42px；聚焦使用 2px 钴蓝轮廓、2px 偏移。
- **记忆编辑**：9px 圆角、`#c9d3e2` 边线、10px 12px 内边距；正文 textarea 最小高 260px，可纵向调整。冲突与删除确认就地显示文字与恢复操作。

### Navigation

主导航为左对齐的图标与文字行，最小高 42px，选中项使用浅蓝背景和深蓝文字。历史会话当前项使用白色背景；标题截断，改名与删除保持独立入口。在支持悬停的桌面，非当前行的操作在悬停或键盘焦点进入后显示。手机抽屉支持关闭按钮、遮罩、Escape、焦点约束及关闭后的焦点恢复。

### Plan switch / Status labels

Plan 使用紧凑文字按钮与 26px × 16px 开关轨道；开启后背景与轨道转为蓝色。任务、记忆状态采用文字色与必要的小状态点，不统一包装成胶囊标签。示例仅呈现视觉状态，实际切换和任务数据仍由应用提供。

### Cards / Containers

子任务卡片为整行可点击的白底细描边容器，12px 圆角、15px 16px 内边距，悬停改浅色背景和更清楚的边线。工具结果与执行报告采用 10px 圆角辅助面，内容按需展开。记忆条目默认透明，选中后使用浅钴蓝面；文档正文直接呈现在工作面上。

### AI 工作伙伴

黑色 bloub SVG 是签名组件，状态文案与动画使用同一个 ViewModel 的活动数据。SVG 阶段依次映射为 idle、thinking、sleep、orbit、burst、wide、comet、notify、alert；后台工作也使用 orbit。动画本身隐藏于辅助技术，旁边的文字通过 `role="status"` 提供信息。

欢迎状态的 SVG 舞台保留桌面 220px、中等窗口 200px、手机 180px；视口高度不超过 660px 时缩为 150px。已有会话与记忆页的工作伙伴位于桌面导航底部，舞台为 156px，短屏缩为 112px；宽度不超过 760px 时移入 64px 顶栏，舞台为 72px。舞台包含动画留白，不应将这些值误报为黑色角色本体的直径。

欢迎、桌面侧栏与手机顶栏之间只挂载一个伙伴动画实例。工作伙伴仍显示真实状态文字，同时把正文上方空间交还给聊天与记忆阅读。

伙伴最多每秒采样 30 帧；页面隐藏时暂停，系统启用减少动态效果时显示对应阶段的代表帧。全局同时取消 CSS 动画与过渡。bloub 轨道原生的 SVG 渐变属于动画资产，界面不另加装饰渐变；不得误将此规则解释为删除原始动画细节。

## Do's and Don'ts

### Do:
- **Do** 使用既有语义色；placeholder 与辅助文字使用 `muted`。
- **Do** 在文字状态旁保留伙伴，并由真实活动数据驱动形态。
- **Do** 维持正文行距、局部滚动与窄屏抽屉的键盘焦点行为。
- **Do** 为可操作项提供悬停、聚焦、禁用及必要的错误反馈。
- **Do** 保留减少动态效果与页面隐藏时暂停的行为。

### Don't:
- **Don't** 把截图中的验证示例、数量或运行状态变成产品默认内容。
- **Don't** 为工作伙伴另加独占正文高度的状态带，或同时挂载多个动画实例。
- **Don't** 用装饰网格、霓虹或新增背景渐变覆盖安静的阅读工作面。
- **Don't** 把 sidecar 的合成色阶、未使用变量或局部色差当成已统一的全局规范。
- **Don't** 把当前操作界面的系统标题字体扩展为独立品牌展示字体规范。
