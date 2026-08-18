import { useEffect, useState, type FormEvent } from 'react'
import type { Settings, SettingsInput } from '../../models/workbench'

type SettingsDialogProps = { authRequired: boolean; settings: Settings | null; pending: boolean; onClose: () => void; onSaveToken: (token: string) => void; onSaveSettings: (input: SettingsInput) => void; onTest: () => Promise<string> }

/** 本地令牌与模型设置对话框。 */
export function SettingsDialog({ authRequired, settings, pending, onClose, onSaveToken, onSaveSettings, onTest }: SettingsDialogProps) {
  const [token, setToken] = useState('')
  const [provider, setProvider] = useState(settings?.provider ?? 'litellm')
  const [model, setModel] = useState(settings?.model ?? '')
  const [baseUrl, setBaseUrl] = useState(settings?.baseUrl ?? '')
  const [apiKey, setApiKey] = useState('')
  const [workspace, setWorkspace] = useState(settings?.defaultWorkspace ?? '')
  const [logLevel, setLogLevel] = useState(settings?.logLevel ?? 'INFO')
  const [testResult, setTestResult] = useState('')
  useEffect(() => { if (settings) { setProvider(settings.provider); setModel(settings.model); setBaseUrl(settings.baseUrl); setWorkspace(settings.defaultWorkspace); setLogLevel(settings.logLevel) } }, [settings])
  function submit(event: FormEvent) { event.preventDefault(); onSaveSettings({ provider, model, baseUrl, apiKey: apiKey || undefined, defaultWorkspace: workspace, logLevel }) }
  return <div className="dialog-backdrop"><section className="settings-dialog"><header><div><h2>工作台设置</h2><p>API Key 只发送给本地后端，前端不保存。</p></div>{!authRequired && <button type="button" onClick={onClose}>×</button>}</header>
    <div className="token-setup"><label>本地访问令牌<input type="password" value={token} onChange={(event) => setToken(event.target.value)} placeholder={authRequired ? '请输入 local_access_token 内容' : '输入新令牌可重新连接'} /></label><button type="button" disabled={!token.trim()} onClick={() => onSaveToken(token)}>连接本地服务</button><small>令牌由后端启动时生成，用于阻止其他网页调用本地服务。</small></div>
    {settings && <form onSubmit={submit}><div className="form-grid"><label>模型渠道<input value={provider} onChange={(event) => setProvider(event.target.value)} /></label><label>模型名称<input value={model} onChange={(event) => setModel(event.target.value)} required /></label><label>API 地址<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label><label>API Key<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={settings.hasApiKey ? settings.apiKeyMasked : '未配置'} /></label><label>默认工作区<input value={workspace} onChange={(event) => setWorkspace(event.target.value)} /></label><label>日志级别<select value={logLevel} onChange={(event) => setLogLevel(event.target.value)}><option>DEBUG</option><option>INFO</option><option>WARNING</option><option>ERROR</option></select></label></div>{testResult && <div className="test-result">{testResult}</div>}<footer><button type="button" disabled={pending} onClick={() => { setTestResult('测试中…'); void onTest().then((value) => setTestResult(`连接成功：${value}`)).catch(() => setTestResult('连接失败，请检查配置')) }}>测试连接</button><button className="primary-button" type="submit" disabled={pending}>保存设置</button></footer></form>}
  </section></div>
}

