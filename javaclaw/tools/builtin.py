from javaclaw.tools.registry import register


@register(schema={
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取本地文件的内容",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要读取的文件路径"
                }
            },
            "required": ["path"]
        }
    }
})
def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"错误：文件不存在 {path}"
