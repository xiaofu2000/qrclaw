"""零外部依赖的中英混合 BM25 + 字段加权本地检索器。

用于：
1. WikiMemory 本地检索（名称 > 标签 > 摘要 > 正文），零额外 LLM 请求；
2. Plan 模式候选页粗筛（目录超过预算时先取 Top-K 再交给筛选模型）；
3. MCP 工具目录的中文与中英混合补充检索。
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Iterable, Sequence, TypeVar

T = TypeVar("T")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_COMPOUND_RE = re.compile(r"[a-z0-9]+(?:[-_:/.]+[a-z0-9]+)+")
_WORD_RE = re.compile(r"[a-z0-9]+")

_EN_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "about",
    "and", "or", "as", "it", "its", "this", "that", "these", "those",
    "how", "what", "why", "where", "when", "which", "who", "do", "does", "did",
    "can", "could", "should", "would", "my", "your", "our", "their",
})

_QUERY_STOP_PHRASES = (
    "为什么", "怎么样", "怎么", "如何", "什么", "哪个", "哪些",
    "多少", "请问", "帮我", "一下", "可以", "能不能", "是不是", "有没有",
)

_CJK_STOP_CHARS = frozenset(
    "的了呢吗吧啊呀哦呃我你他她它们这那哪什怎个是和与或及把被让给在从对于也都就还又很更最不没叫请帮"
)


@dataclass(frozen=True)
class WeightedField:
    """单个待检索字段及其权重。"""

    text: str
    weight: float = 1.0
    exact_boost: float = 0.0


@dataclass(frozen=True)
class QueryToken:
    """切分后的查询词项及类型权重。"""

    term: str
    weight: float
    kind: str  # "compound" | "word" | "bigram" | "char"


@dataclass(frozen=True)
class RankedItem[T]:
    """排序结果条目。"""

    item: T
    score: float


def has_cjk(text: str) -> bool:
    """判断文本是否包含中文字符。"""
    return bool(_CJK_RE.search(text))


def _extract_doc_counts(text: str) -> tuple[dict[str, int], int]:
    """将文档字段切分为词频表（英文复合词 + 单词 + 中文单字 + 中文双字 bigram）。"""
    if not text:
        return {}, 0
    folded = text.casefold()
    counts: dict[str, int] = {}
    length = 0

    for match in _COMPOUND_RE.finditer(folded):
        tok = match.group(0)
        counts[tok] = counts.get(tok, 0) + 1
        length += 1

    for match in _WORD_RE.finditer(folded):
        tok = match.group(0)
        counts[tok] = counts.get(tok, 0) + 1
        length += 1

    for match in _CJK_RE.finditer(folded):
        seg = match.group(0)
        seg_len = len(seg)
        for ch in seg:
            counts[ch] = counts.get(ch, 0) + 1
            length += 1
        for idx in range(seg_len - 1):
            bg = seg[idx : idx + 2]
            counts[bg] = counts.get(bg, 0) + 1
            length += 1

    return counts, length


def tokenize_query(
    query: str,
    *,
    extra_stop_phrases: Iterable[str] = (),
    extra_en_stopwords: Iterable[str] = (),
) -> list[QueryToken]:
    """切分用户查询，剥离疑问虚词但保留短查询兜底。"""
    raw = query.strip().casefold()
    if not raw:
        return []

    cleaned = raw
    for phrase in (*extra_stop_phrases, *_QUERY_STOP_PHRASES):
        if phrase:
            cleaned = cleaned.replace(phrase.casefold(), " ")

    en_stops = _EN_STOPWORDS | {w.casefold() for w in extra_en_stopwords}
    tokens = _build_query_tokens(cleaned, filter_stops=True, en_stops=en_stops)
    if not tokens:
        tokens = _build_query_tokens(raw, filter_stops=False, en_stops=frozenset())
    return tokens


def _build_query_tokens(
    text: str,
    *,
    filter_stops: bool,
    en_stops: frozenset[str],
) -> list[QueryToken]:
    seen: dict[str, QueryToken] = {}

    def _add(term: str, weight: float, kind: str) -> None:
        prev = seen.get(term)
        if prev is None or weight > prev.weight:
            seen[term] = QueryToken(term=term, weight=weight, kind=kind)

    compounds = [m.group(0) for m in _COMPOUND_RE.finditer(text)]
    for tok in compounds:
        _add(tok, 2.4, "compound")

    words = [m.group(0) for m in _WORD_RE.finditer(text)]
    non_stop_words = [w for w in words if w not in en_stops] if filter_stops else words
    effective_words = non_stop_words if (non_stop_words or _CJK_RE.search(text)) else words
    for tok in effective_words:
        _add(tok, 1.8, "word")

    for match in _CJK_RE.finditer(text):
        seg = match.group(0)
        if filter_stops:
            # 用空格替换停用单字以切开子段，避免跨虚词拼出伪双字词
            sub_segs = "".join(" " if ch in _CJK_STOP_CHARS else ch for ch in seg).split()
        else:
            sub_segs = [seg]
        for sub in sub_segs:
            if len(sub) >= 2:
                for idx in range(len(sub) - 1):
                    _add(sub[idx : idx + 2], 2.0, "bigram")
            for ch in sub:
                _add(ch, 0.45, "char")

    return list(seen.values())


def rank_documents(
    query: str,
    items: Sequence[T],
    extract_fields: callable,
    *,
    k1: float = 1.5,
    b: float = 0.75,
    limit: int | None = None,
    extra_stop_phrases: Iterable[str] = (),
    extra_en_stopwords: Iterable[str] = (),
) -> list[RankedItem[T]]:
    """对候选对象集合执行字段加权 BM25 + 短语命中排序。

    extract_fields(item) -> Sequence[WeightedField]
    """
    raw_query = query.strip()
    if not raw_query or not items:
        return []

    q_folded = raw_query.casefold()
    q_tokens = tokenize_query(
        raw_query,
        extra_stop_phrases=extra_stop_phrases,
        extra_en_stopwords=extra_en_stopwords,
    )
    if not q_tokens:
        return []

    n_docs = len(items)
    # 预解析所有文档的字段词频
    parsed_docs: list[list[tuple[WeightedField, dict[str, int], int, str]]] = []
    field_lengths_by_slot: dict[int, int] = {}
    df: dict[str, int] = {qt.term: 0 for qt in q_tokens}

    for item in items:
        fields: Sequence[WeightedField] = extract_fields(item)
        doc_fields: list[tuple[WeightedField, dict[str, int], int, str]] = []
        matched_in_doc: set[str] = set()
        for slot, wf in enumerate(fields):
            counts, length = _extract_doc_counts(wf.text)
            folded_text = wf.text.casefold() if wf.text else ""
            doc_fields.append((wf, counts, length, folded_text))
            field_lengths_by_slot[slot] = field_lengths_by_slot.get(slot, 0) + length
            for qt in q_tokens:
                if qt.term in counts:
                    matched_in_doc.add(qt.term)
        for term in matched_in_doc:
            df[term] += 1
        parsed_docs.append(doc_fields)

    avgdl_by_slot = {
        slot: max(1.0, total / n_docs) for slot, total in field_lengths_by_slot.items()
    }
    idf: dict[str, float] = {
        qt.term: math.log(1.0 + (n_docs - freq + 0.5) / (freq + 0.5))
        for qt, freq in ((qt, df[qt.term]) for qt in q_tokens)
    }

    strong_query_terms = {qt.term for qt in q_tokens if qt.kind in ("compound", "word", "bigram")}
    has_strong_query_terms = bool(strong_query_terms)
    single_char_query = len(q_folded) == 1

    scored: list[RankedItem[T]] = []
    for idx, (item, doc_fields) in enumerate(zip(items, parsed_docs)):
        score = 0.0
        strong_hits = 0
        matched_strong_terms: set[str] = set()
        char_hits: set[str] = set()
        substring_hit = False

        for slot, (wf, counts, length, folded_text) in enumerate(doc_fields):
            if not folded_text:
                continue
            avgdl = avgdl_by_slot.get(slot, 1.0)
            norm = 1.0 - b + b * (length / avgdl)

            # 完整查询或实体名称互含加分
            if q_folded in folded_text:
                substring_hit = True
                if folded_text == q_folded:
                    score += wf.weight * 8.0 + wf.exact_boost * 2.0
                else:
                    score += wf.weight * 3.0 + wf.exact_boost
            elif slot == 0 and len(folded_text) > 1 and folded_text in q_folded:
                # 查询语句中明确点名了页面标题（如 "Osteomeles 和 Medinilla 哪个分布更广？"）
                substring_hit = True
                score += wf.weight * 4.0 + wf.exact_boost

            for qt in q_tokens:
                tf = counts.get(qt.term, 0)
                if tf <= 0:
                    continue
                if qt.kind in ("compound", "word", "bigram"):
                    strong_hits += 1
                    matched_strong_terms.add(qt.term)
                else:
                    char_hits.add(qt.term)
                tf_norm = (tf * (k1 + 1.0)) / (tf + k1 * norm)
                score += wf.weight * qt.weight * idf[qt.term] * tf_norm

        # 防单字噪声误命中：长查询若既无子串命中、也无英文词/双字词命中且单字命中不足 2 个，视为无证据
        if not substring_hit and not single_char_query:
            if has_strong_query_terms and strong_hits == 0 and len(char_hits) < 2:
                continue
            if not has_strong_query_terms and len(char_hits) < 1:
                continue

        # 多关键词协同覆盖率加权（命中全部核心查询词的文档显著优先于只命中部分标题词的文档）
        if len(strong_query_terms) >= 2 and not substring_hit:
            coverage = len(matched_strong_terms) / len(strong_query_terms)
            score *= 0.2 + 0.8 * (coverage ** 2)

        if score > 0:
            scored.append(RankedItem(item=item, score=score))

    scored.sort(key=lambda r: r.score, reverse=True)
    if limit is not None:
        return scored[:limit]
    return scored


def best_snippet_offset(content: str, query: str, *, context_before: int = 80, window: int = 160) -> int:
    """在正文中定位最密集的查询词命中位置，返回建议读取的起始字符 offset。"""
    if not content or not query or not query.strip():
        return 0
    raw = query.strip()
    exact = re.search(re.escape(raw), content, re.IGNORECASE)
    if exact:
        return max(0, exact.start() - context_before)

    q_tokens = tokenize_query(raw)
    if not q_tokens:
        return 0

    folded = content.casefold()
    hits: list[tuple[int, float]] = []
    for qt in q_tokens:
        # 优先按复合词、英文词和双字词定位；单字权重低，仅作辅助聚集
        start = 0
        while True:
            pos = folded.find(qt.term, start)
            if pos < 0:
                break
            hits.append((pos, qt.weight))
            start = pos + max(1, len(qt.term))
            if len(hits) >= 500:
                break
        if len(hits) >= 500:
            break

    if not hits:
        return 0

    hits.sort(key=lambda x: x[0])
    best_pos = hits[0][0]
    best_weight = -1.0
    right = 0
    window_weight = 0.0

    for left in range(len(hits)):
        while right < len(hits) and hits[right][0] - hits[left][0] <= window:
            window_weight += hits[right][1]
            right += 1
        if window_weight > best_weight:
            best_weight = window_weight
            best_pos = hits[left][0]
        window_weight -= hits[left][1]

    return max(0, best_pos - context_before)
