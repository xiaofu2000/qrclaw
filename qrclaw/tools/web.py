import requests
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.web_search.runtime import run_web_search, WebSearchError
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.web")

class WebSearchArgs(BaseModel):
    query: str = Field(description="搜索关键词，用自然语言描述想查找的内容")

class WebFetchArgs(BaseModel):
    url: str = Field(..., description="要抓取的网页 URL，例如 https://example.com")

@register(description="联网搜索，获取最新信息，适合查找新闻、文档、技术资料", args_model=WebSearchArgs)
def web_search(query: str) -> str:
    """
    执行 Web 搜索，自动选择可用的 Provider（例如 Tavily）
    """
    logger.debug(f"联网搜索: {query}")
    try:
        # 调用 Web Search Runtime
        response = run_web_search(query=query, max_results=5)
        
        # 格式化输出为 Markdown
        lines = [f"### 搜索结果 (Provider: {response.provider})"]
        if response.answer:
            lines.append(f"\n**AI 摘要:**\n{response.answer}\n")
            
        if not response.results:
            return "未找到相关结果"

        for i, r in enumerate(response.results, 1):
            lines.append(f"{i}. [{r.title}]({r.url})")
            snippet = r.snippet.replace("\n", " ").strip()
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
def web_fetch(url: str) -> str:
    logger.debug(f"抓取网页正文: {url}")
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {
            "Accept": "application/json", 
            "X-Return-Format": "markdown"
        }
        response = requests.get(jina_url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            content = data.get("data", {}).get("content", "")
            if not content:
                content = data.get("data", {}).get("text", "正文为空")
            logger.info(f"网页抓取成功: {url}, 长度: {len(content)}")
            return content
        else:
            fallback_text = requests.get(jina_url, timeout=30).text
            logger.info(f"网页抓取(降级)成功: {url}, 长度: {len(fallback_text)}")
            return fallback_text
    except Exception as e:
        error_msg = f"网页抓取失败: {e}"
        logger.error(error_msg)
        return error_msg
