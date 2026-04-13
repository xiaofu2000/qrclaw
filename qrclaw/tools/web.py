"""
Web Tools Module - 改进版

改进功能：
- SSRF 防护：阻止私有/内网地址 (10.x, 172.16.x, 192.168.x, localhost)
- LLM 内容压缩：大页面自动分块处理 (>5000 字符触发，>500k 分块合成)
- 多后端支持：Firecrawl > Tavily > Exa > Jina 降级 fallback
- Base64 图片清理：移除干扰 token 的 base64 编码图片
- 超时控制：防止大页面长时间阻塞

依赖项：
- LLM 压缩需要 litellm（通过 qrclaw.llm.chat）
- 多后端需要对应 API Key：FIRECRAWL_API_KEY / TAVILY_API_KEY / EXA_API_KEY
"""

import os
import re
import asyncio
import socket
import ipaddress
from typing import Optional, Tuple
from urllib.parse import urlparse
import requests
import httpx
from pydantic import BaseModel, Field

from qrclaw.tools.registry import register
from qrclaw.web_search.runtime import run_web_search, WebSearchError
from qrclaw.logger import get_logger
from qrclaw.providers import provider

logger = get_logger("qrclaw.tools.web")


# ─── LLM 内容压缩配置 ────────────────────────────────────────────────────────

DEFAULT_MIN_LENGTH_FOR_SUMMARIZATION = 5000
MAX_CONTENT_SIZE = 2_000_000  # 2MB 硬上限
CHUNK_THRESHOLD = 500_000     # 500k 以上启用分块
CHUNK_SIZE = 100_000          # 每块 100k 字符
MAX_OUTPUT_SIZE = 5000        # 最终输出上限


# ─── SSRF 防护 ──────────────────────────────────────────────────────────────

# 已知内部 hostname 黑名单
_BLOCKED_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "169.254.169.254",  # AWS/GCP 元数据端点
    "metadata.aws.internal",
})

# CGNAT 范围 (RFC 6598)
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# Benchmark/Carrier Grade NAT 测试地址 (RFC 2544)
_BENCHMARK_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def _is_blocked_ip(ip_str: str) -> bool:
    """
    检查 IP 是否为私有/内网地址。
    
    阻止范围：
    - RFC 1918 私有地址：10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
    - Loopback：127.0.0.0/8, ::1
    - Link-local：169.254.0.0/16, fe80::/10
    - CGNAT：100.64.0.0/10
    - Multicast/Unspecified
    
    注意：不阻止 198.18.x.x（DNS 污染测试网络），因为公共网站可能被错误解析到该范围。
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        
        # 内网地址（RFC 1918）
        if ip.is_private:
            return True
        
        # Loopback
        if ip.is_loopback:
            return True
        
        # Link-local（如 169.254.x.x）
        if ip.is_link_local:
            return True
        
        # Reserved
        if ip.is_reserved:
            return True
        
        # Multicast / Unspecified
        if ip.is_multicast or ip.is_unspecified:
            return True
        
        # CGNAT (100.64.0.0/10)
        if ip in _CGNAT_NETWORK:
            return True
        
        # 注意：不阻止 _BENCHMARK_NETWORK (198.18.0.0/15)
        # 因为该范围常被 DNS 污染用于测试，在公共网站上可能出现
        # 阻止它会导致所有正常网站被误杀
        
        return False
    except ValueError:
        return False


def _is_safe_url(url: str) -> Tuple[bool, str]:
    """
    检查 URL 是否安全（阻止 SSRF 攻击）。
    
    安全标准：
    - 非私有 IP (10.x, 172.16-31.x, 192.168.x)
    - 非 loopback (127.x, localhost)
    - 非 link-local (169.254.x)
    - 非 CGNAT (100.64-127.x)
    - 非已知内部 hostname
    
    Returns:
        Tuple[bool, str]: (是否安全, 原因说明)
    
    注意：198.18.x.x (Benchmark 网络) 可能因 DNS 污染出现，此时发出警告但不阻止。
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower()
        
        if not hostname:
            return False, "空 hostname"
        
        # 检查已知内部 hostname
        if hostname in _BLOCKED_HOSTNAMES:
            return False, f"内部 hostname: {hostname}"
        
        # 尝试 DNS 解析并检查 IP
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            # DNS 解析失败，安全起见阻止
            return False, f"DNS 解析失败: {hostname}"
        
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            
            # 检查是否在 Benchmark 网络 (198.18.x.x)
            if ip_str.startswith("198.18."):
                # Benchmark 网络通常是 DNS 污染，警告但不阻止
                logger.warning(f"SSRF 注意：{hostname} 解析到 Benchmark 地址 {ip_str}（可能是 DNS 污染）")
                continue
            
            if _is_blocked_ip(ip_str):
                return False, f"私有地址: {hostname} -> {ip_str}"
        
        return True, "安全"
    
    except Exception as exc:
        logger.warning(f"SSRF 防护：URL 安全检查异常 {url}: {exc}")
        return False, f"检查异常: {exc}"


def _is_safe_url_simple(url: str) -> bool:
    """简化版：仅返回是否安全，不返回原因"""
    safe, _ = _is_safe_url(url)
    return safe


# ─── Base64 图片清理 ────────────────────────────────────────────────────────

def _clean_base64_images(text: str) -> str:
    """
    移除 base64 编码图片，减少 token 占用。
    
    匹配格式：
    - (data:image/png;base64,...) 带括号
    - data:image/[type];base64,... 不带括号
    """
    # 带括号的 base64 图片
    pattern_with_parens = r'\(data:image/[^;]+;base64,[A-Za-z0-9+/=]+\)'
    text = re.sub(pattern_with_parens, '[图片已移除]', text)
    
    # 不带括号的 base64 图片
    pattern_without_parens = r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+'
    text = re.sub(pattern_without_parens, '[图片已移除]', text)
    
    return text


# ─── LLM 内容压缩 ────────────────────────────────────────────────────────────

def _call_llm_sync(system_prompt: str, user_prompt: str, max_tokens: int = 20000) -> Optional[str]:
    """
    同步调用 LLM 进行内容处理。
    使用 qrclaw.providers 中的 litellm provider。
    """
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        # 通过 provider 调用（支持同步和异步）
        response = provider.chat(messages, temperature=0.1)
        return response.content if response else None
    except Exception as e:
        logger.warning(f"LLM 调用失败: {e}")
        return None


def _process_content_with_llm(
    content: str,
    url: str = "",
    title: str = "",
    min_length: int = DEFAULT_MIN_LENGTH_FOR_SUMMARIZATION,
) -> Optional[str]:
    """
    使用 LLM 处理网页内容，生成压缩摘要。
    
    处理策略：
    - < 5000 字符：直接返回原文
    - 5000 - 500k 字符：单次 LLM 摘要
    - > 500k 字符：分块处理后合成
    
    Args:
        content: 原始内容
        url: 来源 URL（用于上下文）
        title: 页面标题（用于上下文）
        min_length: 触发 LLM 处理的最短长度
    
    Returns:
        处理后的 markdown 文本，或 None（内容太短）
    """
    content_len = len(content)
    
    # 硬上限：超过 2MB 拒绝处理
    if content_len > MAX_CONTENT_SIZE:
        size_mb = content_len / 1_000_000
        logger.warning(f"内容过大 ({size_mb:.1f}MB > 2MB)，拒绝处理")
        return f"[内容过大无法处理: {size_mb:.1f}MB。请使用更具体的 URL 或搜索更聚焦的来源。]"
    
    # 内容太短，跳过处理
    if content_len < min_length:
        return None
    
    # 构建上下文
    context_parts = []
    if title:
        context_parts.append(f"标题: {title}")
    if url:
        context_parts.append(f"来源: {url}")
    context_str = "\n".join(context_parts) + "\n\n" if context_parts else ""
    
    # 分块处理大内容
    if content_len > CHUNK_THRESHOLD:
        logger.info(f"内容较大 ({content_len} 字符)，使用分块处理...")
        return _process_large_content_chunked(content, context_str)
    
    # 标准单次处理
    return _summarize_content(content, context_str)


def _summarize_content(content: str, context_str: str) -> Optional[str]:
    """对内容进行 LLM 摘要"""
    system_prompt = """你是一个网页内容分析专家。你的任务是为网页内容创建一个全面但简洁的摘要。

要求：
1. 保留所有关键信息（事实、数据、见解、可操作信息）
2. 使用 markdown 格式，包含标题、列表、引用等
3. 保留重要的引用、代码片段、具体细节
4. 输出应该易于扫描和理解

目标是保留所有重要信息的同时大幅减少长度。"""
    
    user_prompt = f"""请处理以下网页内容并创建 markdown 摘要：

{context_str}内容：
{content}

创建摘要，保留所有关键信息和重要细节。"""
    
    result = _call_llm_sync(system_prompt, user_prompt)
    
    if result:
        # 强制截断到上限
        if len(result) > MAX_OUTPUT_SIZE:
            result = result[:MAX_OUTPUT_SIZE] + "\n\n[... 摘要已截断 ...]"
        
        original_len = len(content)
        final_len = len(result)
        ratio = (final_len / original_len * 100) if original_len > 0 else 100
        logger.info(f"内容压缩: {original_len} -> {final_len} 字符 ({ratio:.1f}%)")
    
    return result


def _summarize_chunk_sync(idx: int, chunk: str, context_str: str, total: int) -> Tuple[int, Optional[str]]:
    """
    同步摘要单个块（在 ThreadPoolExecutor 中运行）。
    """
    try:
        chunk_info = f"[第 {idx + 1}/{total} 部分]"
        summary = _summarize_chunk(chunk, context_str, chunk_info)
        return idx, summary
    except Exception as e:
        logger.warning(f"块 {idx + 1}/{total} 处理失败: {e}")
        return idx, None


def _process_large_content_chunked(content: str, context_str: str) -> str:
    """
    处理超大内容：分块 -> 并行摘要 -> 合成。
    
    策略：
    1. 将内容分成 100k 字符的块
    2. 每个块单独调用 LLM 获取摘要
    3. 将所有摘要合并后再次调用 LLM 合成
    """
    chunk_size = CHUNK_SIZE
    chunks = []
    for i in range(0, len(content), chunk_size):
        chunks.append(content[i:i + chunk_size])
    
    logger.info(f"分块处理：拆分为 {len(chunks)} 个块，每块 ~{chunk_size} 字符")
    
    # 使用线程池并行摘要所有块
    import concurrent.futures
    
    summaries = [None] * len(chunks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(chunks), 4)) as executor:
        futures = [
            executor.submit(_summarize_chunk_sync, i, chunk, context_str, len(chunks))
            for i, chunk in enumerate(chunks)
        ]
        for future in concurrent.futures.as_completed(futures):
            idx, summary = future.result()
            summaries[idx] = summary
    
    # 收集成功的摘要
    valid_summaries = []
    for idx, summary in enumerate(summaries):
        if summary:
            valid_summaries.append(f"## 第 {idx + 1} 部分\n{summary}")
    
    if not valid_summaries:
        logger.warning("所有块处理均失败，回退到原文截断")
        return content[:MAX_OUTPUT_SIZE] + f"\n\n[内容过长，已截断前 {MAX_OUTPUT_SIZE} 字符]"
    
    logger.info(f"成功处理 {len(valid_summaries)}/{len(chunks)} 个块")
    
    # 单块成功，直接返回
    if len(valid_summaries) == 1:
        return valid_summaries[0]
    
    # 多块：合成最终摘要
    return _synthesize_summaries(valid_summaries, context_str)


def _summarize_chunk(chunk: str, context_str: str, chunk_info: str) -> Optional[str]:
    """对单个块进行摘要"""
    system_prompt = """你是文档分块处理专家。你的任务是从大文档的【单个部分】提取关键信息。

重要原则：
1. 不要写引言或结论（这是部分内容）
2. 专注于提取所有关键事实、数据、见解
3. 保留原始格式的重要引用、代码片段
4. 使用结构化格式便于后续合成
5. 不要提及"如上所述"、"见下文"等跨节引用"""
    
    user_prompt = f"""从以下文档的【部分内容】中提取关键信息：

{context_str}{chunk_info}

内容：
{chunk}

提取所有重要信息，使用结构化格式。"""
    
    result = _call_llm_sync(system_prompt, user_prompt, max_tokens=10000)
    
    if result:
        logger.debug(f"块摘要完成: {len(chunk)} -> {len(result)} 字符")
    
    return result


def _synthesize_summaries(summaries: list, context_str: str) -> str:
    """将多个块摘要合成为最终摘要"""
    combined = "\n\n---\n\n".join(summaries)
    
    system_prompt = """你是内容合成专家。你的任务是将多个部分摘要合成为一个连贯、完整的最终摘要。

要求：
1. 移除重复信息
2. 保留所有关键事实、数据、可操作信息
3. 保持清晰的结构
4. 总长度控制在合理范围内（< 5000 字符）

输出应该是一个完整、连贯的摘要，而非松散的部分集合。"""
    
    user_prompt = f"""将以下各部分摘要合成为一个完整摘要：

{context_str}各部分摘要：
{combined}

创建统一、结构化的最终摘要。"""
    
    result = _call_llm_sync(system_prompt, user_prompt)
    
    if not result:
        # 合成失败，回退到拼接
        logger.warning("摘要合成失败，回退到拼接")
        fallback = "\n\n".join(summaries)
        if len(fallback) > MAX_OUTPUT_SIZE:
            fallback = fallback[:MAX_OUTPUT_SIZE] + "\n\n[已截断...]"
        return fallback
    
    if len(result) > MAX_OUTPUT_SIZE:
        result = result[:MAX_OUTPUT_SIZE] + "\n\n[... 摘要已截断 ...]"
    
    return result


# ─── 多后端获取 ──────────────────────────────────────────────────────────────

def _has_api_key(name: str) -> bool:
    """检查环境变量是否有非空值"""
    val = os.getenv(name, "").strip()
    return bool(val)


def _get_fetch_backend() -> str:
    """
    确定使用哪个 fetch 后端。
    
    优先级：Firecrawl > Tavily > Exa > Jina
    """
    if _has_api_key("FIRECRAWL_API_KEY"):
        return "firecrawl"
    if _has_api_key("TAVILY_API_KEY"):
        return "tavily"
    if _has_api_key("EXA_API_KEY"):
        return "exa"
    return "jina"  # 默认降级到 Jina


async def _fetch_with_firecrawl(url: str) -> Tuple[str, str]:
    """使用 Firecrawl 获取内容"""
    try:
        from firecrawl import Firecrawl
        api_key = os.getenv("FIRECRAWL_API_KEY")
        api_url = os.getenv("FIRECRAWL_API_URL", "").strip()
        
        kwargs = {"api_key": api_key}
        if api_url:
            kwargs["api_url"] = api_url
        
        client = Firecrawl(**kwargs)
        
        # 使用线程池避免阻塞
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: client.scrape(url=url, formats=["markdown"])
        )
        
        if result and hasattr(result, "markdown"):
            title = getattr(result, "title", "") or ""
            content = result.markdown or ""
        elif isinstance(result, dict):
            title = result.get("title", "") or ""
            content = result.get("markdown", "") or ""
        else:
            title = ""
            content = str(result) if result else ""
        
        return title, content
    except Exception as e:
        logger.debug(f"Firecrawl 获取失败: {e}")
        raise


async def _fetch_with_tavily(url: str) -> Tuple[str, str]:
    """使用 Tavily 获取内容"""
    try:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise ValueError("TAVILY_API_KEY not set")
        
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.tavily.com/extract",
                json={"urls": [url], "api_key": api_key},
            )
            response.raise_for_status()
            data = response.json()
            
            results = data.get("results", [{}])
            if results:
                result = results[0]
                title = result.get("title", "") or ""
                content = result.get("raw_content", "") or result.get("content", "") or ""
            else:
                title = ""
                content = ""
            
            return title, content
    except Exception as e:
        logger.debug(f"Tavily 获取失败: {e}")
        raise


async def _fetch_with_exa(url: str) -> Tuple[str, str]:
    """使用 Exa 获取内容"""
    try:
        from exa_py import Exa
        api_key = os.getenv("EXA_API_KEY")
        if not api_key:
            raise ValueError("EXA_API_KEY not set")
        
        client = Exa(api_key=api_key)
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: client.get_contents([url], text=True)
        )
        
        if result and hasattr(result, "results") and result.results:
            item = result.results[0]
            title = getattr(item, "title", "") or ""
            content = getattr(item, "text", "") or ""
        elif isinstance(result, dict):
            results = result.get("results", [{}])
            if results:
                title = results[0].get("title", "") or ""
                content = results[0].get("text", "") or ""
            else:
                title = ""
                content = ""
        else:
            title = ""
            content = ""
        
        return title, content
    except Exception as e:
        logger.debug(f"Exa 获取失败: {e}")
        raise


async def _fetch_with_jina(url: str) -> Tuple[str, str]:
    """使用 Jina AI Reader 获取内容（最后降级方案）"""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {
            "Accept": "application/json",
            "X-Return-Format": "markdown"
        }
        
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(jina_url, headers=headers)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    title = data.get("data", {}).get("title", "") or ""
                    content = data.get("data", {}).get("content", "") or ""
                except Exception:
                    # JSON 解析失败，使用纯文本
                    text = response.text
                    title = ""
                    content = text
            else:
                # HTTP 错误，使用纯文本
                content = response.text
                title = ""
            
            return title, content
    except Exception as e:
        logger.debug(f"Jina 获取失败: {e}")
        raise


async def _multi_backend_fetch(url: str) -> Tuple[str, str, str]:
    """
    多后端获取网页内容，自动 fallback。
    
    优先级：Firecrawl > Tavily > Exa > Jina
    
    Returns:
        Tuple[str, str, str]: (title, content, backend_used)
    """
    # SSRF 防护检查
    if not _is_safe_url_simple(url):
        return "", "", "blocked"
    
    backends = [
        ("firecrawl", _fetch_with_firecrawl),
        ("tavily", _fetch_with_tavily),
        ("exa", _fetch_with_exa),
        ("jina", _fetch_with_jina),
    ]
    
    last_error = None
    for backend_name, fetch_func in backends:
        try:
            logger.debug(f"尝试 {backend_name} 获取: {url}")
            title, content = await asyncio.wait_for(fetch_func(url), timeout=60)
            
            if content:
                logger.info(f"{backend_name} 获取成功: {url} ({len(content)} 字符)")
                return title, content, backend_name
        except asyncio.TimeoutError:
            logger.warning(f"{backend_name} 获取超时: {url}")
            last_error = "Timeout"
        except Exception as e:
            logger.debug(f"{backend_name} 获取失败: {e}")
            last_error = str(e)
    
    # 所有后端都失败
    error_msg = last_error or "Unknown error"
    logger.error(f"所有后端获取失败: {url}, 最后错误: {error_msg}")
    return "", "", f"failed:{error_msg}"


# ─── 同步封装 ────────────────────────────────────────────────────────────────

def _multi_backend_fetch_sync(url: str) -> str:
    """
    同步版本的多后端获取（供 web_fetch 使用）。
    内部使用 httpx 同步客户端。
    """
    # SSRF 防护检查
    if not _is_safe_url_simple(url):
        return f"[SSRF 防护：URL 指向私有/内网地址，已被阻止]"
    
    # 确保 URL 有协议
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    
    # 尝试 Jina（最可靠的降级方案）
    try:
        return _fetch_with_jina_sync(url)
    except Exception as e:
        logger.debug(f"Jina 获取失败，尝试降级: {e}")
    
    # 尝试其他后端
    return _fetch_with_fallback_sync(url)


def _fetch_with_jina_sync(url: str) -> str:
    """同步 Jina 获取"""
    jina_url = f"https://r.jina.ai/{url}"
    headers = {
        "Accept": "application/json",
        "X-Return-Format": "markdown"
    }
    
    response = requests.get(jina_url, headers=headers, timeout=30)
    
    if response.status_code == 200:
        try:
            data = response.json()
            content = data.get("data", {}).get("content", "")
            if not content:
                content = data.get("data", {}).get("text", "正文为空")
            return content
        except Exception:
            return response.text
    else:
        return response.text


def _fetch_with_fallback_sync(url: str) -> str:
    """同步降级获取（遍历所有可用后端）"""
    # Tavily
    if _has_api_key("TAVILY_API_KEY"):
        try:
            api_key = os.getenv("TAVILY_API_KEY")
            response = requests.post(
                "https://api.tavily.com/extract",
                json={"urls": [url], "api_key": api_key},
                timeout=60
            )
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [{}])
                if results:
                    content = results[0].get("raw_content", "") or results[0].get("content", "")
                    if content:
                        return content
        except Exception as e:
            logger.debug(f"Tavily 降级失败: {e}")
    
    # Firecrawl
    if _has_api_key("FIRECRAWL_API_KEY"):
        try:
            from firecrawl import Firecrawl
            api_key = os.getenv("FIRECRAWL_API_KEY")
            api_url = os.getenv("FIRECRAWL_API_URL", "").strip()
            
            kwargs = {"api_key": api_key}
            if api_url:
                kwargs["api_url"] = api_url
            
            client = Firecrawl(**kwargs)
            result = client.scrape(url=url, formats=["markdown"])
            
            if result:
                if hasattr(result, "markdown"):
                    return result.markdown or ""
                elif isinstance(result, dict):
                    return result.get("markdown", "") or ""
        except Exception as e:
            logger.debug(f"Firecrawl 降级失败: {e}")
    
    # Exa
    if _has_api_key("EXA_API_KEY"):
        try:
            from exa_py import Exa
            client = Exa(api_key=os.getenv("EXA_API_KEY"))
            result = client.get_contents([url], text=True)
            
            if result and hasattr(result, "results") and result.results:
                return result.results[0].text or ""
        except Exception as e:
            logger.debug(f"Exa 降级失败: {e}")
    
    return "[无法获取网页内容，所有后端均失败]"


# ─── 参数模型 ────────────────────────────────────────────────────────────────

class WebSearchArgs(BaseModel):
    query: str = Field(description="搜索关键词，用自然语言描述想查找的内容")
    max_results: int = Field(default=5, description="最大结果数")


class WebFetchArgs(BaseModel):
    url: str = Field(..., description="要抓取的网页 URL，例如 https://example.com")
    use_compression: bool = Field(default=True, description="是否使用 LLM 压缩大页面内容")


# ─── 工具函数 ────────────────────────────────────────────────────────────────

@register(description="联网搜索，获取最新信息，适合查找新闻、文档、技术资料", args_model=WebSearchArgs)
def web_search(query: str, max_results: int = 5) -> str:
    """
    执行 Web 搜索，自动选择可用的 Provider（例如 Tavily）
    """
    logger.debug(f"联网搜索: {query}")
    try:
        # 调用 Web Search Runtime
        response = run_web_search(query=query, max_results=max_results)
        
        # 格式化输出为 Markdown
        lines = [f"### 搜索结果 (Provider: {response.provider})"]
        if response.answer:
            lines.append(f"\n**AI 摘要:**\n{response.answer}\n")
            
        if not response.results:
            return "未找到相关结果"

        for i, r in enumerate(response.results, 1):
            lines.append(f"{i}. [{r.title}]({r.url})")
            # 清理 Base64 图片 + 处理 snippet
            snippet = _clean_base64_images(r.snippet)
            snippet = snippet.replace("\n", " ").strip()
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            lines.append(f"   > {snippet}\n")
            
        result = "\n".join(lines)
        logger.info(f"搜索成功: {query}, 找到 {response.count} 条结果")
        return result

    except WebSearchError as e:
        error_msg = f"搜索配置错误: {e}"
        logger.warning(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"搜索执行失败: {e}"
        logger.error(f"搜索失败: {query}, 错误: {e}", exc_info=True)
        return error_msg


@register(description="访问指定网页并提取纯净的 Markdown 正文，适合阅读文章、文档", args_model=WebFetchArgs)
def web_fetch(url: str, use_compression: bool = True) -> str:
    """
    抓取网页正文，支持多后端 fallback 和 LLM 压缩。
    
    特性：
    - SSRF 防护：阻止私有/内网地址
    - 多后端：Firecrawl > Tavily > Exa > Jina
    - LLM 压缩：>5000 字符自动摘要
    - Base64 清理：移除干扰图片
    """
    logger.debug(f"抓取网页正文: {url}")
    
    try:
        # SSRF 防护检查
        if not _is_safe_url_simple(url):
            return "[SSRF 防护：URL 指向私有/内网地址，已被阻止]"
        
        # 确保 URL 有协议
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        
        # 多后端获取（同步版本只返回 content）
        content = _multi_backend_fetch_sync(url)
        
        if content.startswith("[SSRF 防护"):
            return content
        
        if not content or content.startswith("[网页抓取失败") or content.startswith("[无法获取"):
            return content
        
        # 清理 base64 图片
        content = _clean_base64_images(content)
        
        # LLM 压缩（如果启用且内容足够长）
        if use_compression:
            processed = _process_content_with_llm(content, url)
            if processed:
                content = processed
        
        logger.info(f"网页抓取成功: {url}, 长度: {len(content)}")
        return content
        
    except Exception as e:
        error_msg = f"网页抓取失败: {e}"
        logger.error(error_msg)
        return error_msg
