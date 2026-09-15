import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'
import { RunRepository } from '../src/repositories/runRepository'
import { WorkbenchRepository } from '../src/repositories/workbenchRepository'
import { ApiError, ApiService } from '../src/services/apiService'
import { useWorkbenchViewModel } from '../src/viewmodels/useWorkbenchViewModel'

describe('工作台错误状态', () => {
  beforeEach(() => {
    localStorage.setItem('qrclaw.localAccessToken', 'test-token')
    vi.spyOn(ApiService.prototype, 'health').mockResolvedValue({
      status: 'ok',
      service: 'qrclaw-local-workbench',
    })
    vi.spyOn(ApiService.prototype, 'capabilities').mockResolvedValue({
      protocol_version: '1',
      features: [],
      limits: { max_concurrent_runs: 1 },
    })
    vi.spyOn(WorkbenchRepository.prototype, 'listConversations').mockResolvedValue([
      {
        id: 'conv_test',
        title: '测试会话',
        workspacePath: '/project',
        createdAt: '2026-08-18T08:00:00Z',
        updatedAt: '2026-08-18T08:00:00Z',
      },
    ])
    vi.spyOn(WorkbenchRepository.prototype, 'getSettings').mockResolvedValue({
      provider: 'litellm',
      model: 'test-model',
      baseUrl: '',
      hasApiKey: true,
      apiKeyMasked: '••••test',
      defaultWorkspace: '/project',
      logLevel: 'INFO',
      sandboxEnabled: false,
    })
    vi.spyOn(WorkbenchRepository.prototype, 'getMessages').mockResolvedValue([])
    vi.spyOn(WorkbenchRepository.prototype, 'getActiveConversationId').mockReturnValue(null)
    vi.spyOn(WorkbenchRepository.prototype, 'getRunId').mockReturnValue(null)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('运行冲突提示五秒后自动清除', async () => {
    vi.spyOn(RunRepository.prototype, 'start').mockRejectedValue(
      new ApiError('run_conflict', '本地服务已有运行中的任务', true, 409),
    )
    const { result } = renderHook(() => useWorkbenchViewModel())
    await waitFor(() => expect(result.current.loading).toBe(false))

    vi.useFakeTimers()
    await act(async () => {
      await result.current.sendMessage('执行新任务')
    })
    expect(result.current.error).toBe('本地服务已有运行中的任务')

    act(() => vi.advanceTimersByTime(5000))
    expect(result.current.error).toBeNull()
  })
})
