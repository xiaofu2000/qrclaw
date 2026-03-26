"""
临时测试脚本：验证 Google Vertex AI 文本生成和图片生成
运行前请将 YOUR_API_KEY 替换为你的真实 API Key
"""

import os
import sys

API_KEY = "AQ.Ab8RN6I0hjRf4rcaxISFVYZtJN4LUkbHSogWHkiGOaV1DapE_g"

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
os.environ["GOOGLE_CLOUD_PROJECT"] = "enhanced-voyage-491210-n9"
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"

try:
    from google import genai
    from google.genai.types import HttpOptions, GenerateContentConfig, Modality
    from PIL import Image
    from io import BytesIO
    print("[依赖] 导入成功")
except ImportError as e:
    print(f"[错误] 缺少依赖: {e}")
    print("请运行: pip install google-genai Pillow --break-system-packages")
    sys.exit(1)

# ── 测试1：文本生成 ────────────────────────────────
print("\n=== 测试1：文本生成 (gemini-2.5-flash) ===")
try:
    client = genai.Client(
        http_options=HttpOptions(api_version="v1"),
        api_key=API_KEY
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="用一句话介绍你自己",
    )
    print("[成功] 文本回复:", response.text)
except Exception as e:
    print("[失败] 文本生成错误:", e)

# ── 测试2：图片生成 ────────────────────────────────
print("\n=== 测试2：图片生成 (gemini-2.5-flash-image) ===")
try:
    client2 = genai.Client(api_key=API_KEY)
    response2 = client2.models.generate_content(
        model="gemini-2.5-flash-image",
        contents="一只可爱的橘猫坐在窗台上看夕阳",
        config=GenerateContentConfig(
            response_modalities=[Modality.TEXT, Modality.IMAGE],
        ),
    )
    for part in response2.candidates[0].content.parts:
        if part.text:
            print("[成功] 文字回复:", part.text)
        elif part.inline_data:
            output_path = "/Users/fuqingrong/.qrclaw/test_output.png"
            image = Image.open(BytesIO(part.inline_data.data))
            image.save(output_path)
            print(f"[成功] 图片已保存到: {output_path}")
except Exception as e:
    print("[失败] 图片生成错误:", e)

print("\n=== 测试完成 ===")
