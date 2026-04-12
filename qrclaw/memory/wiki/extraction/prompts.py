"""
Extraction Prompts - 提取提示词模板

从 memory_extraction.py 行 109-139 迁移
"""

EXTRACTION_PROMPT_TEMPLATE = """\
你是一位经验丰富的 Wiki 维护者。

## 你的职责
分析对话历史，提取有价值的信息，整理成结构化的 Wiki 页面。

## 写入规则
1. **页面命名**：使用清晰、简洁的命名（如 `Python 虚拟环境` 而非 `关于 Python 虚拟环境的介绍`）
2. **内容格式**：使用 Markdown 格式，包含适当的标题、列表、代码块
3. **标签规范**：每个页面必须有 tags，包含 1-3 个标签
4. **关联页面**：使用 `related` 字段标注关联页面名称

## 操作类型
- `create`：创建新页面（页面不存在时）
- `append`：追加到现有页面（页面已存在但内容不完整时）
- `skip`：跳过（信息无价值或重复时）

## 输入信息
### 现有 Wiki 索引
{index_md}

### 对话历史
{messages_text}

## 输出要求
请用 JSON 格式输出提取结果，包含：
- `needs_update`：是否需要更新 Wiki
- `pages`：待处理页面列表，每个页面包含 action/name/content/description/tags/related

只输出 JSON，不要有其他内容。\
"""

CONSOLIDATION_PROMPT_TEMPLATE = """\
你是一位技术文档编辑，负责整理和合并页面内容。

## 任务
将新的内容片段合并到现有页面中，去除重复，保持连贯性。

## 现有页面内容
{existing_content}

## 新内容片段
{new_content}

## 页面名称
{page_name}

## 输出要求
请输出整理后的 JSON：
- `content`：整理后的完整正文
- `description`：页面描述（50字以内）
- `tags`：标签列表

只输出 JSON，不要有其他内容。\
"""
