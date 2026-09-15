import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { NewConversationDialog } from '../src/features/dialogs/NewConversationDialog'

describe('新建任务工作区选择', () => {
  it('通过目录列表选择工作区，不要求手工输入路径', async () => {
    const user = userEvent.setup()
    const onBrowseWorkspace = vi.fn().mockImplementation(async (path = '') => ({
      currentPath: path || '/Users/test',
      parentPath: '/Users',
      homePath: '/Users/test',
      directories: path.endsWith('/project')
        ? []
        : [
            { name: '.cache', path: '/Users/test/.cache' },
            { name: 'project', path: '/Users/test/project' },
          ],
    }))
    const onSubmit = vi.fn()

    render(
      <NewConversationDialog
        defaultWorkspace="/Users/test"
        pending={false}
        onBrowseWorkspace={onBrowseWorkspace}
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    )

    expect(screen.queryByPlaceholderText('/Users/name/project')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '选择文件夹' }))
    expect(await screen.findByRole('region', { name: '选择工作区' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /.cache/ })).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: /project/ }))
    await user.click(screen.getByRole('button', { name: '选择此文件夹' }))
    await user.click(screen.getByRole('button', { name: '创建' }))

    expect(onSubmit).toHaveBeenCalledWith({
      title: '新任务',
      workspacePath: '/Users/test/project',
    })
  })
})
