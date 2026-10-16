---
version: 1
slug: "web-src-app-app-tsx"
primary_target: "web/src/app/App.tsx"
related_targets: ["web/src/components","web/src/features","web/src/styles/global.css"]
---

# QRClaw 工作台

Scope: web/src/app/App.tsx 及聊天、导航、记忆、执行过程全部用户界面。
Mode: Operate。日常中文 AI 工作台，桌面优先、窄屏完整可用。用户已确认所有视觉选择由实现者决定，选择代码先行；不再要求用户挑选视觉方案。

## Direction contract

THESIS: 蓝图工作室。把陪伴形象、正在做什么与下一个输入放在同一工作面，替换原先小型角标加单调聊天框的组合。

OWN-WORLD: 在日常明亮桌面环境下使用。蓝灰导航与明亮白色工作纸面；钴蓝仅用于主要动作、当前选择与工作状态。系统中文无衬线保证操作清晰，宽松阅读行距，紧凑工具密度，按控件尺度分配圆角。无装饰网格、渐变或霓虹。

STORY: 进入可看见伙伴和输入入口；发送后伙伴仍可见，内容成为主角；历史、记忆、执行过程各有稳定入口；所有数据和状态来自现有 ViewModel。

FIRST VIEWPORT: 240px 左侧导航贯穿全高，右侧工作面留 16px 外沿，桌面顶栏高 56px。新会话保留 220px 动画与任务引导，伙伴行最大宽 740px，下方输入区外部最大宽 820px。已有会话与记忆页将 156px 伙伴放在导航底部，独立滚动的内容直接接在顶栏下方；短屏（≤660px 高）侧栏伙伴缩为 112px。手机（≤760px）导航与执行面板变为可关闭抽屉，工作伙伴移入 64px 顶栏、舞台为 72px。始终只挂载一个动画实例。

READING SPACE: 记忆页以紧凑工具栏进入阅读，错误提示、任务与提取记录和检查结果集中在工具栏与阅读区之间，共用总高度最高 min(240px, 30%) 的单一滚动辅助区。任务默认收起，报告首次出现时展开且可收起。宽度大于 1000px 时列表与详情各自滚动；≤1000px 时由列表进入单栏详情，返回保留搜索与编辑草稿。焦点切换使用 preventScroll，返回时优先聚焦当前条目，筛选结果无该条目则聚焦搜索框；工作区与阅读区外层使用 overflow: clip 防止程序滚动。

FORM: 自拟候选第 3 项，seed 88ec4645。候选按顺序为创作软件工作台、展馆导视、绘图室工作面、现场记录仪、图书索引、交通调度台、实验记录册。签名交互是由真实任务状态驱动、始终可见的伙伴动画；只改变展示密度，不制造装饰运动。

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Direction judgement

黑胶编码目录 declined：编号替代名称不利于真实会话检索；保留其单一焦点的纪律。
黑白软件营销 declined：不能清楚表达可操作的工作区；保留其文字对比和操作优先级。
流媒体海报墙 declined：会话无真实封面，横向海报浏览干扰工作；保留焦点操作渐进显露。
机场导视 competitive：导航清楚但长文本需要更安静的画面；吸收动作命名的直接性。
网格字样 declined：中文长回复不适合构造字体；保留严格对齐和尺度体系。
暗房叠片 declined：倾斜重叠使编辑和阅读变复杂；保留层级明确的中性色阶。

## Constraints

保留全部业务功能、草稿、历史与状态映射，不改后端协议。尊重减少动态效果偏好。无营销图片或虚构数据。提交范围仅限本次界面相关改动，保留无关工作。
