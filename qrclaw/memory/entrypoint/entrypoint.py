"""
记忆入口点管理模块

负责 MEMORY.md 索引文件的维护
参考 Claude Code 的 entrypoint.ts 设计
"""

from pathlib import Path
from datetime import datetime
import re

from qrclaw.logger import get_logger
from qrclaw.memory.types import MemoryFile, MemoryType, MemoryIndex

logger = get_logger("qrclaw.memory.entrypoint")


# Claude Code 常量
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000
MAX_MEMORY_FILES = 200


def parse_memory_index_from_markdown(content: str, memory_dir: Path) -> list[MemoryFile]:
    """
    从 Markdown 内容解析索引列表

    Args:
        content: MEMORY.md 内容
        memory_dir: 记忆根目录

    Returns:
        MemoryFile 列表
    """
    memories = []
    lines = content.split("\n")

    for line in lines:
        # 匹配格式: - [记忆名称](path/to/file.md) — 描述
        if line.strip().startswith("- ["):
            try:
                # 提取链接内容
                bracket_end = line.index("]")
                link_content = line[2:bracket_end]
                name = link_content

                # 提取路径
                paren_start = line.index("(")
                paren_end = line.index(")")
                rel_path = line[paren_start + 1:paren_end]
                file_path = memory_dir / rel_path

                # 提取描述
                dash_idx = line.index("—")
                description = line[dash_idx + 1:].strip()

                # 解析文件获取类型
                if file_path.exists():
                    file_content = file_path.read_text(encoding="utf-8")
                    memory = MemoryFile.from_frontmatter(str(file_path), file_content)
                    if memory:
                        memories.append(memory)
            except (ValueError, IndexError):
                continue

    return memories


def generate_memory_index_markdown(memories: list[MemoryFile]) -> str:
    """
    生成 MEMORY.md 索引内容

    Args:
        memories: 记忆列表

    Returns:
        Markdown 格式的索引内容
    """
    lines = [
        "# QRClaw 记忆索引",
        "",
        "这是 QRClaw 的记忆入口点。每次对话开始时会加载此文件。",
        "",
        "## 记忆文件",
        "",
    ]

    # 按类型分组
    by_type: dict[MemoryType, list[MemoryFile]] = {}
    for memory in memories:
        if memory.type not in by_type:
            by_type[memory.type] = []
        by_type[memory.type].append(memory)

    for memory_type in MemoryType:
        if memory_type in by_type:
            type_names = {
                MemoryType.USER: "用户角色、目标、知识",
                MemoryType.FEEDBACK: "行为指导（避免/保持）",
                MemoryType.PROJECT: "项目上下文、目标、决策",
                MemoryType.REFERENCE: "外部系统指针",
            }
            lines.append(f"### {type_names.get(memory_type, memory_type.value)} ({memory_type.value})")
            lines.append("")
            for memory in sorted(by_type[memory_type], key=lambda m: m.name):
                relative_path = f"{memory.type_path}/{memory.filename}"
                lines.append(f"- [{memory.name}]({relative_path}) — {memory.description}")
            lines.append("")

    lines.append("---")
    lines.append(f"*最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    return "\n".join(lines)


class MemoryEntrypoint:
    """MEMORY.md 入口点管理器"""

    def __init__(self, memory_dir: Path):
        """
        初始化入口点管理器

        Args:
            memory_dir: 记忆根目录
        """
        self.memory_dir = Path(memory_dir)
        self.entrypoint_path = self.memory_dir / "MEMORY.md"
        self._ensure_entrypoint()
        logger.info(f"入口点初始化: {self.entrypoint_path}")

    def _ensure_entrypoint(self):
        """确保入口文件存在"""
        if not self.entrypoint_path.exists():
            default_content = """# QRClaw 记忆索引

这是 QRClaw 的记忆入口点。每次对话开始时会加载此文件。

## 记忆文件

暂无记忆。使用 write_memory 工具保存重要信息。

---
*最后更新: {timestamp}*
"""
            self.entrypoint_path.write_text(
                default_content.format(timestamp=datetime.now().isoformat()),
                encoding="utf-8"
            )
            logger.debug("创建 MEMORY.md 入口文件")

    def load(self) -> str:
        """
        加载入口文件内容

        Returns:
            str: 入口文件内容
        """
        try:
            return self.entrypoint_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"加载入口文件失败: {e}", exc_info=True)
            return ""

    def save(self, content: str) -> bool:
        """
        保存入口文件

        Args:
            content: 入口文件内容

        Returns:
            bool: 是否成功
        """
        try:
            self.entrypoint_path.write_text(content, encoding="utf-8")
            return True
        except Exception as e:
            logger.error(f"保存入口文件失败: {e}", exc_info=True)
            return False

    def rebuild(self, memories: list[MemoryFile]) -> bool:
        """
        重建入口索引

        Args:
            memories: 所有记忆文件列表

        Returns:
            bool: 是否成功
        """
        try:
            content = generate_memory_index_markdown(memories)

            # 检查大小限制
            if len(content) > MAX_ENTRYPOINT_BYTES:
                logger.warning(
                    f"入口文件过大 ({len(content)} bytes)，"
                    f"超过限制 {MAX_ENTRYPOINT_BYTES} bytes"
                )
                content = self._truncate_content(content)

            self.save(content)
            logger.info(f"重建入口索引: {len(memories)} 个记忆")
            return True
        except Exception as e:
            logger.error(f"重建入口索引失败: {e}", exc_info=True)
            return False

    def add_memory(self, memory: MemoryFile) -> bool:
        """
        添加记忆到入口索引

        Args:
            memory: 记忆文件

        Returns:
            bool: 是否成功
        """
        try:
            content = self.load()
            memories = parse_memory_index_from_markdown(content, self.memory_dir)

            # 检查是否已存在
            existing = [m for m in memories if m.name == memory.name]
            if existing:
                # 更新现有条目
                memories = [m for m in memories if m.name != memory.name]

            memories.append(memory)

            # 检查数量限制
            if len(memories) > MAX_MEMORY_FILES:
                logger.warning(
                    f"记忆数量 ({len(memories)}) 超过限制 ({MAX_MEMORY_FILES})"
                )
                # 移除最旧的记忆
                memories.sort(key=lambda m: m.updated_at)
                memories = memories[-MAX_MEMORY_FILES:]

            return self.rebuild(memories)
        except Exception as e:
            logger.error(f"添加记忆到入口索引失败: {e}", exc_info=True)
            return False

    def remove_memory(self, name: str) -> bool:
        """
        从入口索引移除记忆

        Args:
            name: 记忆名称

        Returns:
            bool: 是否成功
        """
        try:
            content = self.load()
            memories = parse_memory_index_from_markdown(content, self.memory_dir)

            # 移除指定记忆
            original_count = len(memories)
            memories = [m for m in memories if m.name != name]

            if len(memories) == original_count:
                logger.warning(f"入口索引中未找到记忆: {name}")
                return False

            return self.rebuild(memories)
        except Exception as e:
            logger.error(f"从入口索引移除记忆失败: {e}", exc_info=True)
            return False

    def update_memory(self, memory: MemoryFile) -> bool:
        """
        更新入口索引中的记忆

        Args:
            memory: 更新后的记忆

        Returns:
            bool: 是否成功
        """
        # 移除旧条目，添加新条目
        self.remove_memory(memory.name)
        return self.add_memory(memory)

    def get_memories(self) -> list[MemoryFile]:
        """
        从入口索引获取所有记忆

        Returns:
            MemoryFile 列表
        """
        try:
            content = self.load()
            return parse_memory_index_from_markdown(content, self.memory_dir)
        except Exception as e:
            logger.error(f"获取入口索引记忆失败: {e}", exc_info=True)
            return []

    def validate(self) -> list[str]:
        """
        验证入口索引的一致性

        Returns:
            警告列表
        """
        warnings = []

        try:
            content = self.load()
            memories = parse_memory_index_from_markdown(content, self.memory_dir)

            # 检查行数限制
            if content.count("\n") > MAX_ENTRYPOINT_LINES:
                warnings.append(
                    f"入口文件行数 ({content.count('+1')}) 超过限制 ({MAX_ENTRYPOINT_LINES})"
                )

            # 检查文件大小
            if len(content.encode("utf-8")) > MAX_ENTRYPOINT_BYTES:
                warnings.append(
                    f"入口文件大小 ({len(content.encode('utf-8'))} bytes) "
                    f"超过限制 ({MAX_ENTRYPOINT_BYTES} bytes)"
                )

            # 检查引用的文件是否存在
            for memory in memories:
                file_path = self.memory_dir / memory.type_path / memory.filename
                if not file_path.exists():
                    warnings.append(f"引用的文件不存在: {memory.name} -> {file_path}")

            if warnings:
                logger.warning(f"入口索引验证发现问题: {len(warnings)} 个警告")
        except Exception as e:
            warnings.append(f"验证入口索引时出错: {e}")

        return warnings

    def _truncate_content(self, content: str) -> str:
        """
        截断过长的内容

        Args:
            content: 原始内容

        Returns:
            截断后的内容
        """
        lines = content.split("\n")

        # 保留头部和摘要信息
        truncated = []
        for line in lines[:MAX_ENTRYPOINT_LINES // 2]:
            truncated.append(line)

        truncated.append("\n... (更多记忆已截断) ...\n")

        # 保留尾部信息
        for line in lines[-MAX_ENTRYPOINT_LINES // 4:]:
            truncated.append(line)

        return "\n".join(truncated)

    def get_stats(self) -> dict:
        """
        获取入口索引统计信息

        Returns:
            统计字典
        """
        content = self.load()
        memories = parse_memory_index_from_markdown(content, self.memory_dir)

        by_type = {}
        for memory in memories:
            type_name = memory.type.value
            if type_name not in by_type:
                by_type[type_name] = 0
            by_type[type_name] += 1

        return {
            "total": len(memories),
            "by_type": by_type,
            "lines": content.count("\n"),
            "bytes": len(content.encode("utf-8")),
            "path": str(self.entrypoint_path),
        }
