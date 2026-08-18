import { Sidebar } from '../components/Sidebar'
import { Topbar } from '../components/Topbar'
import { ToolApprovalDialog } from '../features/approvals/ToolApprovalDialog'
import { ChatPanel } from '../features/chat/ChatPanel'
import { Composer } from '../features/composer/Composer'
import { NewConversationDialog } from '../features/dialogs/NewConversationDialog'
import { InspectorPanel } from '../features/inspector/InspectorPanel'
import { SettingsDialog } from '../features/settings/SettingsDialog'
import { isRunActive } from '../models/workbench'
import type { WorkbenchViewModel } from '../viewmodels/useWorkbenchViewModel'

type WorkbenchViewProps = { viewModel: WorkbenchViewModel }

/** 纯展示工作台：只渲染 ViewModel 状态并转发用户操作。 */
export function WorkbenchView({ viewModel: vm }: WorkbenchViewProps) {
  const approval = vm.snapshot?.pendingApprovals[0] ?? null
  const approvalAgent = approval ? vm.snapshot?.agents.find((item) => item.id === approval.agentId) ?? null : null
  const running = isRunActive(vm.snapshot?.run.status)
  return <div className={`app-shell ${approval ? 'approval-open' : ''}`}>
    <Topbar workspaceTitle={vm.activeConversation?.workspacePath ?? ''} connection={vm.connection} run={vm.snapshot?.run ?? null} canCancel={vm.canCancel} onCancel={() => void vm.cancelRun()} onOpenSettings={() => vm.setSettingsOpen(true)} />
    {vm.error && <div className="error-banner" role="alert"><span>{vm.error}</span><button type="button" onClick={() => void vm.retry()}>重试</button><button type="button" onClick={vm.dismissError}>关闭</button></div>}
    <div className="workspace">
      <Sidebar conversations={vm.conversations} activeId={vm.activeConversation?.id ?? null} disabled={vm.operationPending} onNewConversation={() => vm.setCreateConversationOpen(true)} onSelectConversation={(id) => void vm.selectConversation(id)} onRenameConversation={(id, title) => void vm.renameConversation(id, title)} onDeleteConversation={(id) => void vm.deleteConversation(id)} />
      <main className="chat-column"><div className="conversation-heading"><strong>{vm.activeConversation?.title ?? 'QRClaw 工作台'}</strong>{vm.loading && <span>正在加载…</span>}</div><ChatPanel messages={vm.messages} snapshot={vm.snapshot} selectedAgent={vm.selectedAgent} onSelectAgent={vm.selectAgent} /><Composer onSend={vm.sendMessage} disabled={!vm.canSend} running={running} /></main>
      <InspectorPanel snapshot={vm.snapshot} selectedAgent={vm.selectedAgent} onCloseAgent={() => vm.selectAgent(null)} />
    </div>
    {approval && <ToolApprovalDialog approval={approval} agent={approvalAgent} pending={vm.operationPending} onResolve={(decision) => void vm.resolveApproval(approval.id, decision)} />}
    {vm.createConversationOpen && !vm.authRequired && <NewConversationDialog defaultWorkspace={vm.settings?.defaultWorkspace ?? ''} pending={vm.operationPending} onBrowseWorkspace={vm.browseDirectories} onClose={() => vm.setCreateConversationOpen(false)} onSubmit={(input) => void vm.createConversation(input)} />}
    {vm.settingsOpen && <SettingsDialog authRequired={vm.authRequired} settings={vm.settings} pending={vm.operationPending} onClose={() => vm.setSettingsOpen(false)} onSaveToken={vm.configureAccessToken} onSaveSettings={(input) => void vm.saveSettings(input)} onTest={vm.testModelConnection} />}
  </div>
}
