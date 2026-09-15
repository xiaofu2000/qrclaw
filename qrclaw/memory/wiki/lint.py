"""
WikiLint —— LLM Wiki 知识库质量检查模块

职责：
- 矛盾页面检测（Contradiction Check）
- 孤立页面检测（Orphan Check）
- 缺失交叉引用检测（Broken Link Check）
- 质量评分（Quality Score）

融合背景：LLM Wiki 模式与 QRClaw WikiMemory 的质量保障层
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from qrclaw.memory.wiki.page import WikiPage
    from qrclaw.memory.wiki.wiki_memory import WikiMemory

from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.wiki.lint")

# ── 正则：匹配 [[页面名]] 链接 ─────────────────────────────────────────────────
WIKI_LINK_PATTERN = re.compile(r"\[\[([^\[\]]+)\]\]")


@dataclass
class LintIssue:
    """检查发现的问题"""

    issue_type: str  # "contradiction" | "orphan" | "broken_link"
    page_name: str
    detail: str
    severity: str = "warning"  # "error" | "warning" | "info"


@dataclass
class LintReport:
    """完整检查报告"""

    total_pages: int
    issues: list[LintIssue] = field(default_factory=list)

    # ── 统计属性 ────────────────────────────────────────────────────────────

    @property
    def contradictions(self) -> list[LintIssue]:
        return [i for i in self.issues if i.issue_type == "contradiction"]

    @property
    def orphans(self) -> list[LintIssue]:
        return [i for i in self.issues if i.issue_type == "orphan"]

    @property
    def broken_links(self) -> list[LintIssue]:
        return [i for i in self.issues if i.issue_type == "broken_link"]

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    # ── 质量评分 ────────────────────────────────────────────────────────────

    def quality_score(self) -> int:
        """
        综合检查结果给出 0-100 分质量评分。

        评分规则（伪代码逻辑）：
        - 基础分 100
        - 每发现一个孤立页面：-5 分
        - 每发现一个断裂链接：-3 分
        - 每发现一个矛盾页面：-10 分
        - 分数下限 0
        """
        score = 100
        score -= len(self.orphans) * 5
        score -= len(self.broken_links) * 3
        score -= len(self.contradictions) * 10
        return max(0, score)

    # ── 报告摘要 ────────────────────────────────────────────────────────────

    def summary(self) -> str:
        return (
            f"WikiLint 报告：{self.total_pages} 个页面，"
            f"发现 {len(self.issues)} 个问题"
            f"（矛盾 {len(self.contradictions)}，孤立 {len(self.orphans)}，断链 {len(self.broken_links)}），"
            f"质量评分 {self.quality_score()}/100"
        )


# ── WikiLint 主类 ──────────────────────────────────────────────────────────────


class WikiLint:
    """
    Wiki 知识库质量检查器

    用法：
        lint = WikiLint(wiki)          # wiki: WikiMemory 实例
        report = lint.run_all()        # 运行全部检查
        print(report.quality_score()) # 0-100 分
        print(report.orphans)          # 孤立页面列表
    """

    def __init__(self, wiki: "WikiMemory"):
        """
        初始化检查器

        Args:
            wiki: WikiMemory 实例，调用 wiki.pages（页面列表）、WikiPage.related、wiki.search_pages()
        """
        self.wiki = wiki
        self._pages: list["WikiPage"] = []
        self._page_names: set[str] = set()
        self._link_graph: dict[str, set[str]] = {}  # page -> set of [[linked pages]]
        self._backlinks: dict[str, set[str]] = {}   # page -> set of pages linking to it

    # ── 核心检查入口 ──────────────────────────────────────────────────────────

    def run_all(self) -> LintReport:
        """
        运行全部四项检查，返回完整报告。

        调用流程：
            _load_pages()       → 加载所有页面，构建 page_names
            _build_link_graph() → 解析 [[链接]]，构建正向/反向链接图
            contradiction_check() → 检测矛盾页面
            orphan_check()       → 检测孤立页面
            broken_link_check()  → 检测断裂链接
        """
        self._load_pages()
        self._build_link_graph()

        report = LintReport(total_pages=len(self._pages))
        report.issues.extend(self.contradiction_check())
        report.issues.extend(self.orphan_check())
        report.issues.extend(self.broken_link_check())

        logger.info(report.summary())
        return report

    # ── 内部：页面加载 ─────────────────────────────────────────────────────────

    def _load_pages(self) -> None:
        """从 WikiMemory 加载所有页面，构建 page_names 集合"""
        # 调用 wiki.pages（list_pages()）获取页面列表
        self._pages = self.wiki.list_pages()
        # 构建名称集合，支持模糊匹配
        self._page_names = {p.name for p in self._pages}
        logger.debug(f"WikiLint: 加载 {len(self._pages)} 个页面")

    # ── 内部：链接图构建 ───────────────────────────────────────────────────────

    def _build_link_graph(self) -> None:
        """
        解析所有页面正文中的 [[页面名]] 链接，
        构建正向链接图 self._link_graph 和反向链接图 self._backlinks。
        """
        self._link_graph = {p.name: set() for p in self._pages}
        self._backlinks = {p.name: set() for p in self._pages}

        for page in self._pages:
            # 从正文提取 [[页面名]]
            linked_names = WIKI_LINK_PATTERN.findall(page.content)
            for linked_name in linked_names:
                # 尝试模糊匹配（处理大小写、空格差异）
                matched = self.wiki.fuzzy_find_name(linked_name)
                if matched:
                    self._link_graph[page.name].add(matched)
                    self._backlinks[matched].add(page.name)
                else:
                    # 记录为无效链接（待 broken_link_check 处理）
                    pass

            # 同时处理 related 字段的关联
            for related_name in page.related:
                matched = self.wiki.fuzzy_find_name(related_name)
                if matched:
                    self._link_graph[page.name].add(matched)
                    self._backlinks[matched].add(page.name)

    # ═══════════════════════════════════════════════════════════════════════════
    # 检查 1：矛盾页面检测
    # ═══════════════════════════════════════════════════════════════════════════

    def contradiction_check(self) -> list[LintIssue]:
        """
        矛盾页面检测（Contradiction Check）

        检测策略：
        - 对同一概念在不同页面出现时，对比描述是否冲突
        - 使用 wiki.search_pages() 搜索相关页面
        - 基于关键词/核心描述的比对（伪代码层面）

        返回冲突页面对列表。
        """
        issues: list[LintIssue] = []
        checked_pairs: set[tuple[str, str]] = set()

        for page in self._pages:
            # 策略1：检查 related 页面是否有矛盾描述
            for related_name in page.related:
                related_page = self.wiki.get_page(related_name)
                if not related_page:
                    continue

                # 构造配对 ID（保证双向不重复检查）
                pair = tuple(sorted([page.name, related_name]))
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)

                # 检测矛盾：两个页面对彼此的描述是否不一致
                # 例如：A 说"依赖 B"，B 说"不依赖 A"
                contradiction = self._detect_description_contradiction(page, related_page)
                if contradiction:
                    issues.append(LintIssue(
                        issue_type="contradiction",
                        page_name=page.name,
                        detail=f"与 {related_name} 存在描述矛盾：{contradiction}",
                        severity="warning",
                    ))

            # 策略2：同一概念在多个页面的 description 冲突
            # 通过搜索相同标签的页面进行对比
            for tag in page.tags:
                similar_pages = self.wiki.search_pages(tag)
                for similar in similar_pages:
                    if similar.name == page.name:
                        continue
                    # 检查是否描述同一概念但说法不一致
                    if self._is_same_concept_but_different_desc(page, similar):
                        issues.append(LintIssue(
                            issue_type="contradiction",
                            page_name=page.name,
                            detail=f"与 {similar.name} 标签相同 [{tag}] 但描述不一致",
                            severity="info",
                        ))

        logger.debug(f"WikiLint: 矛盾检测完成，发现 {len(issues)} 个潜在矛盾")
        return issues

    def _detect_description_contradiction(
        self, page_a: "WikiPage", page_b: "WikiPage"
    ) -> Optional[str]:
        """
        检测两个页面之间的描述矛盾。

        伪代码逻辑（实际需要 LLM 判断）：
        - 提取 page_a 对 page_b 的引用描述（搜索 page_a.content 中提到 page_b 的句子）
        - 提取 page_b 对 page_a 的引用描述
        - 判断是否存在逻辑冲突（如一个说"依赖"，另一个说"独立"）
        """
        # 示例矛盾模式（简化版）
        positive_patterns = ["依赖", "基于", "使用", "需要", "调用"]
        negative_patterns = ["独立于", "不依赖", "无关联", "隔离"]

        # 提取 page_a 提到 page_b 的上下文
        mention_a = self._extract_mention_context(page_a.content, page_b.name)
        # 提取 page_b 提到 page_a 的上下文
        mention_b = self._extract_mention_context(page_b.content, page_a.name)

        if not mention_a and not mention_b:
            return None

        # 简化判断：如果 mention 中出现相反词汇则标记矛盾
        has_positive_a = any(p in mention_a for p in positive_patterns)
        has_negative_a = any(p in mention_a for p in negative_patterns)
        has_positive_b = any(p in mention_b for p in positive_patterns)
        has_negative_b = any(p in mention_b for p in negative_patterns)

        if (has_positive_a and has_negative_b) or (has_negative_a and has_positive_b):
            return "一方描述为依赖关系，另一方描述为独立关系"
        if (has_positive_a and has_positive_b) and (page_a.name in mention_b and page_b.name in mention_a):
            # 两者都说依赖对方，可能存在循环依赖警告
            return "可能存在循环依赖"
        return None

    def _extract_mention_context(self, content: str, page_name: str, window: int = 50) -> str:
        """提取 content 中提到 page_name 的上下文片段"""
        pattern = re.compile(re.escape(page_name))
        matches = pattern.finditer(content)
        contexts = []
        for m in matches:
            start = max(0, m.start() - window)
            end = min(len(content), m.end() + window)
            contexts.append(content[start:end])
        return " | ".join(contexts)

    def _is_same_concept_but_different_desc(
        self, page_a: "WikiPage", page_b: "WikiPage"
    ) -> bool:
        """
        判断两个页面是否描述同一概念但说法不一致。

        伪代码逻辑：
        - 检查 description 是否包含共同的关键词
        - 如果共同关键词 > 阈值但描述完全相同 → 重复页面（warning）
        - 如果共同关键词 > 阈值但描述差异大 → 矛盾（warning）
        """
        # 简化：用关键词交集判断是否同概念
        keywords_a = set(page_a.description.lower().split())
        keywords_b = set(page_b.description.lower().split())
        intersection = keywords_a & keywords_b

        # 简单判断：如果 description 有超过 2 个共同词且说法不同
        if len(intersection) >= 2:
            # 描述相似度计算（伪代码：简单字符重叠率）
            similarity = len(intersection) / max(len(keywords_a), len(keywords_b), 1)
            if similarity < 0.5:  # 同概念但描述差异大
                return True
        return False

    # ═══════════════════════════════════════════════════════════════════════════
    # 检查 2：孤立页面检测
    # ═══════════════════════════════════════════════════════════════════════════

    def orphan_check(self) -> list[LintIssue]:
        """
        孤立页面检测（Orphan Check）

        孤立页面定义：
        - 没有 inbound 链接（没有任何其他页面通过 [[链接]] 指向它）
        - 没有 outbound 链接（页面正文不包含任何 [[链接]]）
        - 没有 related 关联

        返回孤立页面名列表。
        """
        issues: list[LintIssue] = []

        for page in self._pages:
            outbound = self._link_graph.get(page.name, set())
            inbound = self._backlinks.get(page.name, set())

            # 判断是否孤立
            is_orphan = len(outbound) == 0 and len(inbound) == 0 and len(page.related) == 0

            if is_orphan:
                issues.append(LintIssue(
                    issue_type="orphan",
                    page_name=page.name,
                    detail="无任何 inbound/outbound 链接，也无 related 关联",
                    severity="warning",
                ))
            # 半孤立：只有单向链接（可作为 info 级别）
            elif len(outbound) == 0 or len(inbound) == 0:
                issues.append(LintIssue(
                    issue_type="orphan",
                    page_name=page.name,
                    detail=f"单向链接（outbound: {len(outbound)}, inbound: {len(inbound)}），缺少反向引用",
                    severity="info",
                ))

        logger.debug(f"WikiLint: 孤立检测完成，发现 {len(issues)} 个孤立/半孤立页面")
        return issues

    # ═══════════════════════════════════════════════════════════════════════════
    # 检查 3：缺失交叉引用检测
    # ═══════════════════════════════════════════════════════════════════════════

    def broken_link_check(self) -> list[LintIssue]:
        """
        缺失交叉引用检测（Broken Link Check）

        检测策略：
        - 提取页面正文中所有 [[页面名]] 格式的链接
        - 检查每个链接是否都有对应的页面文件
        - 返回缺失页面的列表

        返回格式：[("源页面", "[[目标页面]]")]
        """
        issues: list[LintIssue] = []

        for page in self._pages:
            # 提取正文中的 [[页面名]]
            linked_names = WIKI_LINK_PATTERN.findall(page.content)
            for linked_name in linked_names:
                # 检查目标页面是否存在
                if not self.wiki.page_exists(linked_name):
                    # 尝试模糊匹配
                    matched = self.wiki.fuzzy_find_name(linked_name)
                    if matched:
                        # 存在模糊匹配，仅作为 info 级别
                        issues.append(LintIssue(
                            issue_type="broken_link",
                            page_name=page.name,
                            detail=f"[[{linked_name}]] 拼写可能有误，已匹配到 '{matched}'",
                            severity="info",
                        ))
                    else:
                        # 真正的断裂链接
                        issues.append(LintIssue(
                            issue_type="broken_link",
                            page_name=page.name,
                            detail=f"[[{linked_name}]] 不存在（目标页面缺失）",
                            severity="error",
                        ))

            # 同时检查 related 字段中引用不存在的页面
            for related_name in page.related:
                if not self.wiki.page_exists(related_name):
                    matched = self.wiki.fuzzy_find_name(related_name)
                    if matched:
                        issues.append(LintIssue(
                            issue_type="broken_link",
                            page_name=page.name,
                            detail=f"related 中 '{related_name}' 拼写可能有误，已匹配到 '{matched}'",
                            severity="info",
                        ))
                    else:
                        issues.append(LintIssue(
                            issue_type="broken_link",
                            page_name=page.name,
                            detail=f"related 中引用的 '{related_name}' 不存在",
                            severity="error",
                        ))

        logger.debug(f"WikiLint: 断链检测完成，发现 {len(issues)} 个断裂/疑似断裂链接")
        return issues

    # ═══════════════════════════════════════════════════════════════════════════
    # 质量评分
    # ═══════════════════════════════════════════════════════════════════════════

    def quality_score(self) -> int:
        """
        综合上述检查给出 0-100 分质量评分。

        评分维度与权重（伪代码）：
        - 孤立页面扣分：-5 分/个（最多扣 20 分）
        - 断裂链接扣分：-3 分/个（最多扣 15 分）
        - 矛盾页面扣分：-10 分/个（最多扣 30 分）
        - 完整性加分：每增加 1 个双向引用 +1 分（封顶 5 分）
        """
        report = self.run_all()
        return report.quality_score()


# ── 便捷函数 ───────────────────────────────────────────────────────────────────


def lint_wiki(wiki: "WikiMemory") -> LintReport:
    """
    对 WikiMemory 执行完整质量检查。

    用法：
        from qrclaw.memory.wiki import WikiMemory, lint_wiki
        wiki = WikiMemory.for_workspace(Path(".qrclaw/agents/default/memory"))
        report = lint_wiki(wiki)
        print(report.quality_score())  # 0-100
        print(report.orphans)          # 孤立页面列表
    """
    linter = WikiLint(wiki)
    return linter.run_all()
