import { useWorkbenchViewModel } from '../viewmodels/useWorkbenchViewModel'
import { WorkbenchView } from '../views/WorkbenchView'

/** 应用组合根：创建 ViewModel，并交给纯展示工作台。 */
export function App() {
  const viewModel = useWorkbenchViewModel()
  return <WorkbenchView viewModel={viewModel} />
}
