# LLM Wiki 模式与 QRClaw WikiMemory 融合方案

## 1. 页面类型定义

```yaml
type: enum
values:
  - concept    # 概念/知识：定义、术语、原理
  - procedure  # 流程/步骤：操作指南、教程
  - agent      # Agent 相关：角色、技能、工作流
  - rule       # 规则/约束：业务规则、安全策略
  - reference  # 参考资料：API文档、配置说明
```

## 2. 命名规范

```
# 文件名: snake_case + 语义前缀
wiki_<type>_<semantic_name>.md

示例:
  wiki_concept_memory_system.md
  wiki_procedure_code_review.md
  wiki_agent_planner.md
  wiki_rule_security_policy.md
  wiki_reference_api_docs.md

# 页面内 name 字段: 语义化中文标题
name: "记忆系统设计 - 压缩与检索机制"
```

## 3. Frontmatter 格式扩展

```yaml
---
# 基础字段（现有）
name: "页面名称"
description: "一句话描述"
tags: [标签1, 标签2]
related: ["关联页面1", "关联页面2"]
created_at: "ISO时间戳"
updated_at: "ISO时间戳"

# 扩展字段（新增）
type: concept              # 页面类型
llm_context:              # LLM 上下文信息
  confidence: 0.85        # 置信度 0-1
  source: "extraction"    # 来源：extraction/manual/api
  source_ref: "对话ID/文件路径"
  extracted_at: "ISO时间戳"
  version: 1              # 版本号，用于冲突检测

# 语义标签（扩展）
semantic_tags:            # 结构化语义标签
  domain: "memory"        # 领域
  level: "core"           # 层级：core/plugin/user
  stability: "stable"     # 稳定性：stable/experimental

# 关系图谱（扩展）
links:
  - target: "目标页面名"
    relation: "depends_on"  # depends_on/extends/conflicts
    bidirectional: true

# 索引优化
index_priority: 10        # 0-100，影响搜索排序
---
```

## 4. Ingest 操作伪代码

```python
class WikiIngestor:
    """LLM 输出内容写入 WikiMemory"""

    def ingest_structured(self, llm_output: dict, wiki: WikiMemory) -> str:
        """
        接收 LLM 结构化 JSON 输出，upsert 写入 wiki
        """
        # 1. 解析 LLM 输出
        payload = WikiPayload(**llm_output)
        
        # 2. 构建文件名（符合命名规范）
        filename = self._generate_filename(payload)
        
        # 3. 检查冲突
        existing = self._check_conflict(wiki, filename, payload)
        if existing and not self._should_overwrite(existing, payload):
            return self._handle_conflict(existing, payload, wiki)
        
        # 4. 构建 WikiPage（包含扩展 frontmatter）
        page = WikiPage(
            name=payload.name,
            description=payload.description,
            tags=payload.tags,
            related=payload.related or [],
            content=payload.content,
            # 扩展字段
            type=payload.type,
            llm_context=LlmContext(
                confidence=payload.confidence,
                source="structured_json",
                source_ref=payload.source_ref,
                extracted_at=datetime.now().isoformat(),
                version=1
            ),
            semantic_tags=payload.semantic_tags,
            links=payload.links,
            index_priority=payload.priority or 50
        )
        
        # 5. Upsert 保存
        wiki.save_page(page)
        
        return page.name

    def ingest_unstructured(self, text: str, context: dict, wiki: WikiMemory) -> str:
        """
        接收非结构化文本，通过 extraction 模块提取后写入
        """
        # 1. 调用 extraction 模块提取结构化信息
        extraction_result = self._extract_structured(text, context)
        
        # 2. 合并原始文本
        extraction_result.content = text
        
        # 3. 递归调用结构化 ingest
        return self.ingest_structured(extraction_result, wiki)

    def _check_conflict(self, wiki: WikiMemory, filename: str, payload: WikiPayload) -> Optional[WikiPage]:
        """检查同名页面是否存在"""
        # 查询现有页面
        existing_pages = wiki.search_pages(filename)
        return existing_pages[0] if existing_pages else None

    def _handle_conflict(self, existing: WikiPage, new_payload: WikiPayload, wiki: WikiMemory) -> str:
        """冲突处理策略"""
        if new_payload.confidence > existing.llm_context.confidence:
            # 高置信度覆盖低置信度
            new_payload.version = existing.llm_context.version + 1
            return self.ingest_structured(new_payload, wiki)
        elif new_payload.merge_enabled:
            # 合并内容
            merged_content = self._merge_content(existing.content, new_payload.content)
            new_payload.content = merged_content
            return self.ingest_structured(new_payload, wiki)
        else:
            # 创建版本分支
            new_payload.name = f"{new_payload.name} (v{new_payload.version})"
            return self.ingest_structured(new_payload, wiki)

    def _generate_filename(self, payload: WikiPayload) -> str:
        """生成符合规范的文件名"""
        type_prefix = f"wiki_{payload.type}"
        semantic = slugify(payload.name)[:30]
        return f"{type_prefix}_{semantic}.md"
```

## 5. Query 操作伪代码

```python
class WikiQuerier:
    """根据任务上下文检索相关 wiki 页面"""

    def query(self, context: QueryContext, wiki: WikiMemory) -> list[QueryResult]:
        """
        多维度检索并排序返回
        """
        results = []
        
        # 1. 关键词匹配
        if context.keywords:
            keyword_matches = wiki.search_pages(" ".join(context.keywords))
            results.extend(self._to_result(p, "keyword", 0.8) for p in keyword_matches)
        
        # 2. Tag 筛选
        if context.required_tags:
            tag_matches = self._filter_by_tags(wiki, context.required_tags)
            results.extend(self._to_result(p, "tag", 0.9) for p in tag_matches)
        
        # 3. 类型过滤
        if context.page_types:
            results = [r for r in results if r.page.type in context.page_types]
        
        # 4. 相关度排序
        scored_results = self._calculate_relevance(results, context)
        scored_results.sort(key=lambda x: x.score, reverse=True)
        
        # 5. 上下文增强
        return self._enrich_with_context(scored_results, context)

    def _calculate_relevance(self, results: list[QueryResult], context: QueryContext) -> list[QueryResult]:
        """计算综合相关度"""
        for r in results:
            score = 0.0
            
            # 关键词命中权重
            for kw in context.keywords:
                if kw.lower() in r.page.name.lower():
                    score += 0.3
                if kw.lower() in r.page.description.lower():
                    score += 0.15
            
            # Tag 匹配权重
            common_tags = set(context.required_tags) & set(r.page.tags)
            score += 0.2 * len(common_tags)
            
            # 置信度权重
            score += 0.15 * (r.page.llm_context.confidence or 0.5)
            
            # 索引优先级权重
            score += 0.1 * (r.page.index_priority / 100)
            
            # 层级相关性（core > plugin > user）
            level_weights = {"core": 0.15, "plugin": 0.1, "user": 0.05}
            score += level_weights.get(r.page.semantic_tags.level, 0)
            
            r.score = score
        
        return results

    def _enrich_with_context(self, results: list[QueryResult], context: QueryContext) -> list[QueryResult]:
        """添加上下文信息：related pages 展开"""
        enriched = []
        seen = set()
        
        for r in results:
            if r.page.name not in seen:
                enriched.append(r)
                seen.add(r.page.name)
                
                # 展开 related pages
                if context.expand_related:
                    for related_name in r.page.related:
                        if related_name not in seen:
                            related_page = wiki.get_page_by_name(related_name)
                            if related_page:
                                enriched.append(QueryResult(
                                    page=related_page,
                                    match_type="related",
                                    score=r.score * 0.5,  # related 降低权重
                                    match_details={"via": r.page.name}
                                ))
                                seen.add(related_name)
        
        return enriched


@dataclass
class QueryContext:
    keywords: list[str]
    required_tags: list[str] = field(default_factory=list)
    page_types: list[str] = field(default_factory=list)  # concept/procedure/agent/rule/reference
    expand_related: bool = True
    min_confidence: float = 0.0
    max_results: int = 10
```

## 6. Lint 操作伪代码框架

```python
class WikiLinter:
    """
    Wiki 页面质量检查与自动修复
    详细实现见 Step 2
    """

    def lint(self, wiki: WikiMemory) -> LintReport:
        """执行全面检查"""
        report = LintReport()
        
        # 1. 命名规范检查
        report.add_issues(self._check_naming_convention(wiki))
        
        # 2. Frontmatter 完整性检查
        report.add_issues(self._check_frontmatter_fields(wiki))
        
        # 3. 内容质量检查
        report.add_issues(self._check_content_quality(wiki))
        
        # 4. 链接完整性检查
        report.add_issues(self._check_link_integrity(wiki))
        
        # 5. 冲突检测
        report.add_issues(self._check_conflicts(wiki))
        
        return report

    def _check_naming_convention(self, wiki: WikiMemory) -> list[LintIssue]:
        """检查文件名是否符合 wiki_<type>_*.md 规范"""
        issues = []
        for page in wiki.get_all_pages():
            expected_prefix = f"wiki_{page.type}_"
            if not page.filename.startswith(expected_prefix):
                issues.append(LintIssue(
                    severity="warning",
                    page=page.name,
                    message=f"文件名应以 '{expected_prefix}' 开头",
                    fix=self._suggest_filename(page)
                ))
        return issues

    def _check_frontmatter_fields(self, wiki: WikiMemory) -> list[LintIssue]:
        """检查必填字段"""
        required_fields = ["name", "type", "llm_context", "tags"]
        issues = []
        for page in wiki.get_all_pages():
            for field in required_fields:
                if not getattr(page, field, None):
                    issues.append(LintIssue(
                        severity="error",
                        page=page.name,
                        message=f"缺少必填字段: {field}"
                    ))
        return issues

    def _check_content_quality(self, wiki: WikiMemory) -> list[LintIssue]:
        """检查内容质量（详细规则见 Step 2）"""
        pass

    def _check_link_integrity(self, wiki: WikiMemory) -> list[LintIssue]:
        """检查 [[页面名]] 链接是否有效"""
        pass

    def _check_conflicts(self, wiki: WikiMemory) -> list[LintIssue]:
        """检测相似页面，提示合并"""
        pass

    def auto_fix(self, report: LintReport, wiki: WikiMemory) -> FixReport:
        """自动修复可安全修复的问题"""
        pass


@dataclass
class LintIssue:
    severity: str  # error/warning/info
    page: str
    message: str
    fix: Optional[str] = None


@dataclass
class LintReport:
    issues: list[LintIssue] = field(default_factory=list)
    
    def add_issues(self, issues: list[LintIssue]):
        self.issues.extend(issues)
    
    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)
```

## 7. 与现有 WikiMemory 的集成

```python
# 使用示例

# 创建 ingestor
ingestor = WikiIngestor()

# 接收 LLM 结构化输出
llm_output = {
    "type": "concept",
    "name": "向量检索机制",
    "description": "基于语义向量的知识检索原理",
    "content": "...",
    "tags": ["记忆系统", "检索"],
    "confidence": 0.92
}
page_name = ingestor.ingest_structured(llm_output, wiki)

# 查询
querier = WikiQuerier()
context = QueryContext(
    keywords=["记忆", "压缩"],
    page_types=["concept", "procedure"]
)
results = querier.query(context, wiki)

# Lint 检查
linter = WikiLinter()
report = linter.lint(wiki)
if report.has_errors:
    linter.auto_fix(report, wiki)
```

---

## 附录：WikiPage 扩展字段映射

| 新字段 | YAML key | 类型 | 说明 |
|--------|----------|------|------|
| type | `type` | enum | 页面类型 |
| confidence | `llm_context.confidence` | float | 置信度 |
| source | `llm_context.source` | str | 来源标识 |
| domain | `semantic_tags.domain` | str | 领域分类 |
| level | `semantic_tags.level` | str | 层级分类 |
| links | `links` | list[dict] | 关系图谱 |
| priority | `index_priority` | int | 索引优先级 |
