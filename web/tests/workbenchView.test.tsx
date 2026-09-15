import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { WorkbenchViewModel } from '../src/viewmodels/useWorkbenchViewModel'
import { WorkbenchView } from '../src/views/WorkbenchView'
import { createSnapshot } from './fixtures'

/** 创建可注入纯展示 View 的工作台 ViewModel。 */
function createViewModel(overrides: Partial<WorkbenchViewModel> = {}): WorkbenchViewModel {
  return {
    loading: false,
    operationPending: false,
    conversations: [
      {
        id: 'conv_test',
        title: '测试会话',
        workspacePath: '/project',
        createdAt: '2026-08-18T08:00:00Z',
        updatedAt: '2026-08-18T08:00:00Z',
      },
    ],
    activeConversation: {
      id: 'conv_test',
      title: '测试会话',
      workspacePath: '/project',
      createdAt: '2026-08-18T08:00:00Z',
      updatedAt: '2026-08-18T08:00:00Z',
    },
    messages: [],
    snapshot: null,
    connection: 'connected',
    error: null,
    authRequired: false,
    settings: null,
    settingsOpen: false,
    createConversationOpen: false,
    selectedAgent: null,
    canSend: true,
    canCancel: false,
    selectConversation: vi.fn(),
    createConversation: vi.fn(),
    browseDirectories: vi.fn(),
    renameConversation: vi.fn(),
    deleteConversation: vi.fn(),
    sendMessage: vi.fn(),
    cancelRun: vi.fn(),
    resolveApproval: vi.fn(),
    retry: vi.fn(),
    dismissError: vi.fn(),
    configureAccessToken: vi.fn(),
    saveSettings: vi.fn(),
    testModelConnection: vi.fn(),
    setSettingsOpen: vi.fn(),
    setCreateConversationOpen: vi.fn(),
    selectAgent: vi.fn(),
    ...overrides,
  }
}

describe('WorkbenchView 与 ViewModel 交互', () => {
  it('发送输入时只调用 ViewModel，不直接访问网络', async () => {
    const user = userEvent.setup()
    const viewModel = createViewModel()
    render(<WorkbenchView viewModel={viewModel} />)

    const input = screen.getByPlaceholderText('输入任务，Enter 发送，Shift+Enter 换行')
    await user.type(input, '  执行简单 Agent 测试  ')
    await user.click(screen.getByRole('button', { name: '发送' }))

    expect(viewModel.sendMessage).toHaveBeenCalledOnce()
    expect(viewModel.sendMessage).toHaveBeenCalledWith('执行简单 Agent 测试')
    expect(input).toHaveValue('')
  })

  it('运行中展示停止入口并把取消动作交给 ViewModel', async () => {
    const user = userEvent.setup()
    const snapshot = createSnapshot({
      run: {
        ...createSnapshot().run,
        status: 'running',
        route: 'direct',
      },
    })
    const viewModel = createViewModel({
      snapshot,
      canSend: false,
      canCancel: true,
    })
    render(<WorkbenchView viewModel={viewModel} />)

    expect(screen.getByText('任务运行中')).toBeInTheDocument()
    expect(
      screen.getByPlaceholderText('任务运行中，可以等待完成或先停止任务'),
    ).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '停止任务' }))

    expect(viewModel.cancelRun).toHaveBeenCalledOnce()
  })

  it('错误横幅通过 ViewModel 触发重试', async () => {
    const user = userEvent.setup()
    const viewModel = createViewModel({ error: '本地服务连接失败' })
    render(<WorkbenchView viewModel={viewModel} />)

    expect(screen.getByRole('alert')).toHaveTextContent('本地服务连接失败')
    await user.click(screen.getByRole('button', { name: '重试' }))

    expect(viewModel.retry).toHaveBeenCalledOnce()
  })

  it('允许直接关闭已经失效的错误横幅', async () => {
    const user = userEvent.setup()
    const viewModel = createViewModel({ error: '本地服务已有运行中的任务' })
    render(<WorkbenchView viewModel={viewModel} />)

    await user.click(screen.getByRole('button', { name: '关闭' }))

    expect(viewModel.dismissError).toHaveBeenCalledOnce()
  })

  it('设置按钮只改变 ViewModel 中的弹窗状态', async () => {
    const user = userEvent.setup()
    const viewModel = createViewModel()
    render(<WorkbenchView viewModel={viewModel} />)

    await user.click(screen.getByRole('button', { name: '设置' }))

    expect(viewModel.setSettingsOpen).toHaveBeenCalledWith(true)
  })
})
