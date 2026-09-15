"""
工具注册模块测试

测试覆盖：
- 工具注册装饰器
- Schema 生成
- 参数校验
- 工具执行
"""
import json
from pydantic import BaseModel, Field
from qrclaw.tools.registry import (
    register,
    execute,
    get_schemas,
    need_confirm,
    _tools,
)


# 测试用的参数模型
class ToolArgs(BaseModel):
    text: str = Field(description="测试文本")
    count: int = Field(default=1, description="重复次数")


class TestTool:
    """测试工具管理"""

    def setup_method(self):
        """每个测试前清空工具注册表"""
        _tools.clear()

    def test_register_tool(self):
        """测试注册工具"""
        @register(description="测试工具", args_model=ToolArgs)
        def test_tool(text: str, count: int = 1) -> str:
            return text * count
        
        # 验证注册成功
        assert "test_tool" in _tools
        assert _tools["test_tool"]["fn"] == test_tool
        assert _tools["test_tool"]["confirm"] is False

    def test_register_tool_with_confirm(self):
        """测试注册需要确认的工具"""
        @register(description="高风险工具", args_model=ToolArgs, confirm=True)
        def dangerous_tool(text: str, count: int = 1) -> str:
            return text * count
        
        assert need_confirm("dangerous_tool") is True

    def test_need_confirm_default(self):
        """测试默认不需要确认"""
        @register(description="测试工具", args_model=ToolArgs)
        def normal_tool(text: str, count: int = 1) -> str:
            return text * count
        
        assert need_confirm("normal_tool") is False

    def test_need_confirm_nonexistent(self):
        """测试不存在的工具不需要确认"""
        assert need_confirm("nonexistent_tool") is False


class TestSchema:
    """测试 Schema 生成"""

    def setup_method(self):
        """每个测试前清空工具注册表"""
        _tools.clear()

    def test_get_schemas(self):
        """测试获取所有工具 schema"""
        @register(description="工具1", args_model=ToolArgs)
        def tool1(text: str, count: int = 1) -> str:
            return text
        
        @register(description="工具2", args_model=ToolArgs)
        def tool2(text: str, count: int = 1) -> str:
            return text
        
        schemas = get_schemas()
        
        assert len(schemas) == 2
        assert any(s["function"]["name"] == "tool1" for s in schemas)
        assert any(s["function"]["name"] == "tool2" for s in schemas)

    def test_schema_structure(self):
        """测试 schema 结构"""
        @register(description="测试工具", args_model=ToolArgs)
        def test_tool(text: str, count: int = 1) -> str:
            return text
        
        schema = _tools["test_tool"]["schema"]
        
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "test_tool"
        assert schema["function"]["description"] == "测试工具"
        assert "parameters" in schema["function"]
        assert "properties" in schema["function"]["parameters"]
        assert "text" in schema["function"]["parameters"]["properties"]
        assert "count" in schema["function"]["parameters"]["properties"]

    def test_schema_required_fields(self):
        """测试 required 字段"""
        @register(description="测试工具", args_model=ToolArgs)
        def test_tool(text: str, count: int = 1) -> str:
            return text
        
        schema = _tools["test_tool"]["schema"]
        params = schema["function"]["parameters"]
        
        # text 是必填，count 有默认值
        assert "text" in params.get("required", [])
        assert "count" not in params.get("required", [])


class NoArgs(BaseModel):
    """无参数工具的模型"""
    pass


class TestExecution:
    """测试工具执行"""

    def setup_method(self):
        """每个测试前清空工具注册表"""
        _tools.clear()

    def test_execute_tool_success(self):
        """测试成功执行工具"""
        @register(description="测试工具", args_model=ToolArgs)
        def test_tool(text: str, count: int = 1) -> str:
            return text * count
        
        result = execute("test_tool", json.dumps({"text": "hello", "count": 3}))
        
        assert result == "hellohellohello"

    def test_execute_tool_default_args(self):
        """测试使用默认参数执行"""
        @register(description="测试工具", args_model=ToolArgs)
        def test_tool(text: str, count: int = 1) -> str:
            return text * count
        
        result = execute("test_tool", json.dumps({"text": "hi"}))
        
        assert result == "hi"  # count 使用默认值 1

    def test_execute_tool_invalid_json(self):
        """测试无效 JSON 参数"""
        @register(description="测试工具", args_model=ToolArgs)
        def test_tool(text: str, count: int = 1) -> str:
            return text
        
        result = execute("test_tool", "not a json")
        
        assert "错误" in result
        assert "JSON" in result

    def test_execute_tool_missing_required(self):
        """测试缺少必填参数"""
        @register(description="测试工具", args_model=ToolArgs)
        def test_tool(text: str, count: int = 1) -> str:
            return text
        
        result = execute("test_tool", json.dumps({}))
        
        assert "错误" in result or "校验失败" in result

    def test_execute_tool_wrong_type(self):
        """测试参数类型错误"""
        @register(description="测试工具", args_model=ToolArgs)
        def test_tool(text: str, count: int = 1) -> str:
            return text * count
        
        result = execute("test_tool", json.dumps({"text": "hi", "count": "not a number"}))
        
        assert "错误" in result or "校验失败" in result

    def test_execute_nonexistent_tool(self):
        """测试执行不存在的工具"""
        result = execute("nonexistent", json.dumps({}))
        
        assert "错误" in result
        assert "找不到工具" in result

    def test_execute_no_args_tool(self):
        """测试无参数工具"""
        @register(description="无参数工具", args_model=NoArgs)
        def no_args_tool() -> str:
            return "success"
        
        result = execute("no_args_tool", json.dumps({}))
        
        assert result == "success"

    def test_execute_tool_exception(self):
        """测试工具执行异常"""
        @register(description="会报错的工具", args_model=ToolArgs)
        def error_tool(text: str, count: int = 1) -> str:
            raise ValueError("故意报错")
        
        result = execute("error_tool", json.dumps({"text": "test"}))
        
        # 应该捕获异常并返回错误信息
        assert "错误" in result or "校验失败" in result