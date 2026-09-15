import { WorkbenchRepository } from '../src/repositories/workbenchRepository'
import { ApiService } from '../src/services/apiService'

it('恢复聊天时保留用户和最终回复，排除持久化的工具交互', async () => {
  const api = new ApiService('/api/v1', 'test')
  vi.spyOn(api, 'getMessages').mockResolvedValue([
    { message_id: 'user', role: 'user', content: '检查项目' },
    { message_id: 'call', role: 'assistant', content: '读取文件', tool_calls: [{ id: 'tool' }] },
    { message_id: 'tool', role: 'tool', content: '很长的工具输出' },
    { message_id: 'final', role: 'assistant', content: '完成' },
  ])
  expect(await new WorkbenchRepository(api).getMessages('conversation')).toEqual([
    { id: 'user', role: 'user', content: '检查项目' },
    { id: 'final', role: 'assistant', content: '完成' },
  ])
})
