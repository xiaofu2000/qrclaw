"""
临时测试脚本：验证 Google Vertex AI 文本生成和图片生成
认证方式：Application Default Credentials (ADC)
"""

import os
import sys
from io import BytesIO

os.environ["GOOGLE_CLOUD_PROJECT"] = "qrclaw"
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"

try:
    from google import genai
    from google.genai.types import HttpOptions, GenerateContentConfig, Modality
    from PIL import Image
    print("[依赖] 导入成功")
except ImportError as e:
    print(f"[错误] 缺少依赖: {e}")
    print("请运行: pip install google-genai Pillow")
    sys.exit(1)

client = genai.Client(
    vertexai=True,
    project="qrclaw",
    location="us-central1",
    http_options=HttpOptions(api_version="v1"),
)

# ── 测试1：文本生成 ────────────────────────────────
print("\n=== 测试1：文本生成 (gemini-2.5-flash) ===")
try:
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="用一句话介绍你自己",
    )
    print("[成功] 文本回复:", response.text)
except Exception as e:
    print("[失败] 文本生成错误:", e)

# ── 测试2：图片生成 ────────────────────────────────
print("\n=== 测试2：图片生成 (imagen-3.0-generate-002) ===")
try:
    response2 = client.models.generate_images(
        model="imagen-3.0-generate-002",
        prompt="一只可爱的橘猫坐在窗台上看夕阳",
    )
    output_path = "/Users/fuqingrong/.qrclaw/test_output.png"
    image = Image.open(BytesIO(response2.generated_images[0].image.image_bytes))
    image.save(output_path)
    print(f"[成功] 图片已保存到: {output_path}")
except Exception as e:
    print("[失败] 图片生成错误:", e)

print("\n=== 测试完成 ===")
