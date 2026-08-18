import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

type MarkdownContentProps = {
  content: string
  compact?: boolean
}
/** 使用 GitHub Flavored Markdown 安全渲染 Agent 返回内容。 */
export function MarkdownContent({ content, compact = false }: MarkdownContentProps) {
  return (
    <div className={`markdown-content ${compact ? 'compact' : ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
