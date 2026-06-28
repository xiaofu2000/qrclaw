"""
Agent runtime boundary.

This module is intentionally small: it gives new code an explicit object for
the dependencies that were historically accessed through module globals.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rich.console import Console

from qrclaw.memory.context.context_manager import ContextManager
from qrclaw.memory.context.session import Session
from qrclaw.providers.base import LLMProvider
from qrclaw.workspace import Workspace


@dataclass(slots=True)
class AgentRuntime:
    """Runtime dependencies for a single agent execution."""

    session: Session
    workspace: Workspace
    provider: LLMProvider
    context_manager: ContextManager
    console: Optional[Console] = None
    is_sub_agent: bool = False
    auto_confirm: bool = False

