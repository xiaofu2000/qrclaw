export function InspectorPanel() {
  return (
    <aside className="inspector-panel">
      <section>
        <div className="panel-title">运行状态</div>
        <div className="panel-value">本地服务已连接</div>
      </section>
      <section>
        <div className="panel-title">工具调用</div>
        <div className="panel-value muted">等待任务执行</div>
      </section>
      <section>
        <div className="panel-title">文件变更</div>
        <div className="panel-value muted">暂无</div>
      </section>
    </aside>
  )
}

