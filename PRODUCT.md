# QRClaw

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

以中文交互、日常使用 AI 完成工作的用户。本次确认以日常 AI 工作台为使用重心。

## Product Purpose

通过对话发起工作，查看工具与子任务的执行过程，并管理可追溯的长期记忆。小精灵持续清晰可见，以动画反馈真实工作状态。

## Operating Context

现有 React / TypeScript 客户端连接 QRClaw 本地 API。桌面为主要工作空间，窄屏可继续访问所有功能。

## Capabilities and Constraints

保留聊天、历史会话管理、Plan 开关、流式回复、工具与子任务记录、上下文状态、记忆搜索及编辑。保持现有 ViewModel 与 API 协议，UI 与业务逻辑分离。已有会话与草稿不可因切换页面丢失。

## Brand Commitments

名称 QRClaw；中文界面；使用已集成的 bloub 黑色小精灵。用户授权整体替换布局、配色、字体与视觉风格，设计由实现者决定。

## Evidence on Hand

现有 web/src 下的运行界面与 web/tests 功能测试；动画引擎许可和来源在 web/src/vendor/bloub。不得用虚构任务、运行记录或记忆数量代替真实数据。

## Product Principles

- 发起任务和继续对话始终容易找到。
- 工作状态与小精灵由真实会话驱动。
- 长回复可阅读，执行细节可展开。
- 重设计保留既有数据与操作能力。
