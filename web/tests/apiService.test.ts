import { ApiError, ApiService } from '../src/services/apiService'

/** 构造符合后端统一协议的 Fetch 响应。 */
function successResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ data, request_id: 'req_test' }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('ApiService', () => {
  it('健康检查不发送访问令牌', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(successResponse({ status: 'ok', service: 'qrclaw' }))
    const api = new ApiService('/api/v1', 'local-token')

    await expect(api.health()).resolves.toEqual({ status: 'ok', service: 'qrclaw' })

    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/health')
    expect(options?.method).toBe('GET')
    expect(options?.headers).not.toHaveProperty('X-QRClaw-Token')
  })

  it('创建运行时发送令牌、请求 ID 和 snake_case 请求体', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(successResponse({ run_id: 'run_1', status: 'queued' }, 202))
    const api = new ApiService('/api/v1', 'local-token')

    const result = await api.createRun('conv_1', '执行测试', 'client_1')

    expect(result).toEqual({ run_id: 'run_1', status: 'queued' })
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/runs')
    expect(options?.method).toBe('POST')
    expect(options?.headers).toMatchObject({
      'Content-Type': 'application/json',
      'X-QRClaw-Token': 'local-token',
    })
    expect(options?.headers).toHaveProperty('X-Request-ID')
    expect(JSON.parse(String(options?.body))).toEqual({
      conversation_id: 'conv_1',
      content: '执行测试',
      client_request_id: 'client_1',
    })
  })

  it('把协议错误转换为带稳定 code 的 ApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: 'run_conflict',
            message: '已有任务运行中',
            retryable: true,
            details: {},
          },
          request_id: 'req_test',
        }),
        { status: 409 },
      ),
    )
    const api = new ApiService('/api/v1', 'local-token')

    const error = await api.createRun('conv_1', '执行测试', 'client_1').catch((reason) => reason)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      code: 'run_conflict',
      message: '已有任务运行中',
      retryable: true,
      status: 409,
    })
  })

  it('网络异常转换为可重试的本地服务错误', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('连接失败'))
    const api = new ApiService('/api/v1', 'local-token')

    const error = await api.capabilities().catch((reason) => reason)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      code: 'network_error',
      message: '无法连接本地服务',
      retryable: true,
      status: 0,
    })
  })

  it('正确处理删除接口的 204 空响应', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(null, { status: 204 }))
    const api = new ApiService('/api/v1', 'local-token')

    await expect(api.deleteConversation('conv/a')).resolves.toBeUndefined()

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/conversations/conv%2Fa')
    expect(fetchMock.mock.calls[0][1]?.method).toBe('DELETE')
  })
})
