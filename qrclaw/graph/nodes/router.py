"""
Router 节点 —— 判断用户意图路由

职责：
- 分析用户输入，判断是简单任务还是复杂任务
- 简单任务 → 直接执行（ReactLoop）
- 复杂任务 → 计划执行（PlanExecutor + ReactLoop）

路由判断基于：
- 用户输入的复杂度
- 是否需要多步骤
- 是否需要探索/并行
"""
import json
import re
from dataclasses import dataclass, field
from qrclaw.providers import provider
from qrclaw.memory.context.context_manager import get_context_manager
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.nodes.router")

# 用户指令
_ROUTE_INSTRUCTION = """请根据以上对话判断并输出 JSON。"""


@dataclass
class PlanStep:
    id: str
    description: str
    depends_on: list = field(default_factory=list)


@dataclass
class Plan:
    goal: str
    steps: list
    project_path: str = ""


@dataclass
class RouteResult:
    route: str  # "direct" | "plan"
    plan: Plan | None = None


def _sanitize_python_dict(raw: str) -> str:
    """
    将 Python 字典格式转换为标准 JSON 格式。
    
    处理的问题：
    1. 单引号 → 双引号
    2. None/True/False → null/true/false
    3. 尾随逗号
    4. 注释
    """
    result = []
    i = 0
    n = len(raw)
    
    while i < n:
        c = raw[i]
        
        # 处理单引号字符串（必须转换）
        if c == "'":
            # 判断是键/值字符串还是普通单引号
            # 找配对的单引号
            j = i + 1
            while j < n and raw[j] != "'":
                # 处理转义
                if raw[j] == '\\' and j + 1 < n:
                    j += 2
                else:
                    j += 1
            
            if j < n:
                # 提取单引号内的内容并转为双引号
                content = raw[i+1:j]
                # 内容里的双引号需要转义
                content = content.replace('"', '\\"')
                # 单引号转双引号
                result.append('"')
                result.append(content)
                result.append('"')
                i = j + 1
            else:
                # 没找到配对，当普通字符处理
                result.append(c)
                i += 1
        
        # 处理 Python 关键字
        elif c == 'N' and raw[i:i+4] == 'None':
            result.append('null')
            i += 4
        elif c == 'T' and raw[i:i+4] == 'True':
            result.append('true')
            i += 4
        elif c == 'F' and raw[i:i+5] == 'False':
            result.append('false')
            i += 5
        
        # 处理尾随逗号 },] 前多余的逗号
        elif c == ',':
            # 看看后面是什么
            rest = raw[i+1:].lstrip()
            if rest.startswith('}') or rest.startswith(']'):
                # 是尾随逗号，跳过
                i += 1
                # 同时跳过可能的空格
                while i < n and raw[i] in ' \t':
                    i += 1
            else:
                result.append(c)
                i += 1
        
        # 其他字符直接保留
        else:
            result.append(c)
            i += 1
    
    return ''.join(result)


def _strip_markdown_code_blocks(raw: str) -> str:
    """
    去除 Markdown 代码块包裹。
    
    支持：
    ```json
    {...}
    ```
    
    以及：
    ```
    {...}
    ```
    """
    # 去除 ```json ... ``` 或 ``` ... ```
    pattern = r'```(?:json)?\s*\n?(.*?)\n?```'
    match = re.search(pattern, raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    
    # 去除 ``` ... ``` (没有语言标识)
    pattern = r'```\s*\n?(.*?)\n?```'
    match = re.search(pattern, raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    
    return raw


def _extract_json_object(raw: str) -> str | None:
    """
    从文本中提取 JSON 对象。
    
    找到第一个 { 到最后一个 } 之间的内容。
    """
    first_brace = raw.find('{')
    if first_brace == -1:
        return None
    
    # 找最后一个 }
    last_brace = raw.rfind('}')
    if last_brace == -1 or last_brace <= first_brace:
        return None
    
    return raw[first_brace:last_brace + 1]


def _fix_common_json_issues(raw: str) -> str:
    """
    修复常见的 JSON 问题。
    """
    # 移除 <result> 标签
    raw = re.sub(r'<result>.*?</result>', '', raw, flags=re.DOTALL)
    
    # 移除 <output> 标签
    raw = re.sub(r'<output>.*?</output>', '', raw, flags=re.DOTALL)
    
    # 移除 XML 风格标签
    raw = re.sub(r'<[^>]+>', '', raw)
    
    # 移除 JavaScript/Python 注释 //
    raw = re.sub(r'//.*?$', '', raw, flags=re.MULTILINE)
    
    # 移除 /* ... */ 注释
    raw = re.sub(r'/\*.*?\*/', '', raw, flags=re.DOTALL)
    
    # 移除 Python # 注释（行首）
    raw = re.sub(r'^\s*#.*$', '', raw, flags=re.MULTILINE)
    
    # 处理尾随逗号 },] 前
    raw = re.sub(r',(\s*[}\]])', r'\1', raw)
    
    return raw


def _parse_json(raw: str) -> dict:
    """
    防弹版 JSON 解析器。
    
    处理各种不规范的 LLM 输出：
    1. Markdown 代码块包裹
    2. Python 字典格式（单引号）
    3. None/True/False 关键字
    4. 尾随逗号
    5. 注释
    6. XML/HTML 标签
    7. <result> 等特殊标签
    
    按优先级尝试：
    1. 直接解析（标准 JSON）
    2. 清洗后解析（处理常见问题）
    3. 提取 JSON 对象后解析
    4. Python → JSON 转换后解析
    """
    original_raw = raw
    logger.debug(f"原始输出: {raw[:200]}...")
    
    # 步骤 1：直接尝试标准 JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    
    # 步骤 2：去除 Markdown 代码块
    cleaned = _strip_markdown_code_blocks(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    
    # 步骤 3：修复常见问题
    fixed = _fix_common_json_issues(cleaned)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    
    # 步骤 4：提取 JSON 对象
    extracted = _extract_json_object(fixed)
    if extracted:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass
    
    # 步骤 5：从 Python dict 转换
    # 先提取对象（如果有的话）
    if not extracted:
        extracted = _extract_json_object(original_raw)
    
    if extracted:
        # 尝试转换为 JSON
        converted = _sanitize_python_dict(extracted)
        try:
            return json.loads(converted)
        except json.JSONDecodeError:
            pass
        
        # 再尝试修复后解析
        fixed_converted = _fix_common_json_issues(converted)
        try:
            return json.loads(fixed_converted)
        except json.JSONDecodeError:
            pass
    
    # 步骤 6：最后的挣扎 - 暴力替换常见模式
    desperate = original_raw
    
    # 单引号变双引号（但要小心处理已转义的情况）
    # 使用更安全的方式：先处理未转义的单引号
    desperate = re.sub(r"(?<!\\)'", '"', desperate)
    
    # None/True/False 变 JSON 关键字
    desperate = desperate.replace("None", "null")
    desperate = desperate.replace("True", "true")
    desperate = desperate.replace("False", "false")
    
    # 清理尾随逗号
    desperate = re.sub(r',(\s*[}\]])', r'\1', desperate)
    
    try:
        result = json.loads(desperate)
        logger.warning("JSON 解析成功（暴力修复模式）")
        return result
    except json.JSONDecodeError:
        pass
    
    # 所有方法都失败了
    raise ValueError(f"无法解析 JSON，输入: {original_raw[:200]}...")


def _parse_plan(data: dict, fallback_input: str) -> Plan:
    goal = data.get("goal", fallback_input[:50])
    project_path = data.get("project_path", "")
    steps = [
        PlanStep(
            id=s["id"],
            description=s["description"],
            depends_on=s.get("depends_on", []),
        )
        for s in data.get("steps", [])
    ]
    return Plan(goal=goal, steps=steps, project_path=project_path)


class RouterNode:

    def run(self, user_input: str) -> RouteResult:
        logger.info(f"Router 判断路由: {user_input[:60]}...")
        ctx = get_context_manager()
        messages = ctx.build_messages("router", route_instruction=_ROUTE_INSTRUCTION)

        try:
            response = provider.chat(messages, tools=None, json_mode=True, temperature=0.1)
            data = _parse_json(response.content)

            route_val = data.get("route", "direct")
            if route_val not in ("direct", "plan"):
                logger.warning(f"Router 返回未知 route: {route_val}，降级为 direct")
                route_val = "direct"

            if route_val == "plan":
                if not data.get("steps"):
                    logger.warning("Router 返回 plan 但 steps 为空，降级为 direct")
                    return RouteResult(route="direct")
                p = _parse_plan(data, user_input)
                logger.info(f"路由结果: plan，目标: {p.goal}，项目路径: {p.project_path}，共 {len(p.steps)} 步")
                for s in p.steps:
                    dep_str = f"依赖 {s.depends_on}" if s.depends_on else "可并行"
                    logger.debug(f"  Step {s.id}: {s.description} [{dep_str}]")
                return RouteResult(route="plan", plan=p)

            logger.info("路由结果: direct")
            return RouteResult(route="direct")

        except Exception as e:
            logger.warning(f"Router 解析失败: {e}，降级为 direct")
            return RouteResult(route="direct")
