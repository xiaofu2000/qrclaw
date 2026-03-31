#!/usr/bin/env python3
"""
微信公众号文章发布脚本
将 Markdown 文章发布到微信公众号草稿箱
"""

import json
import re
import hashlib
import time
import requests
from pathlib import Path


def load_wechat_config():
    """从配置文件加载微信凭据"""
    config_path = Path.home() / ".qrclaw" / "agents" / "wechat-pipeline" / ".secrets" / "wechat-config.json"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            return config.get("WECHAT_APP_ID"), config.get("WECHAT_APP_SECRET")
    return None, None


# 微信公众号配置
APP_ID, APP_SECRET = load_wechat_config()

if not APP_ID or not APP_SECRET:
    raise Exception("未找到微信凭据，请检查配置文件")

print(f"✓ 加载微信配置: AppID={APP_ID}")

# 全局变量
access_token = None
token_expire_time = 0


def get_access_token():
    """获取微信公众号 access_token"""
    global access_token, token_expire_time
    
    # 检查缓存
    if access_token and time.time() < token_expire_time:
        return access_token
    
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={APP_ID}&secret={APP_SECRET}"
    response = requests.get(url)
    data = response.json()
    
    if "access_token" in data:
        access_token = data["access_token"]
        token_expire_time = time.time() + data["expires_in"] - 300  # 提前5分钟过期
        print(f"✓ 获取 access_token 成功")
        return access_token
    else:
        raise Exception(f"获取 access_token 失败: {data}")


def markdown_to_wechat_html(md_content: str) -> str:
    """将 Markdown 转换为微信公众号 HTML"""
    html = md_content
    
    # 移除 frontmatter
    html = re.sub(r'^---\n.*?\n---\n', '', html, flags=re.DOTALL)
    
    # 标题
    html = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
    
    # 粗体
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    
    # 斜体
    html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)
    
    # 代码块
    html = re.sub(r'```(\w*)\n(.*?)```', r'<pre><code class="\1">\2</code></pre>', html, flags=re.DOTALL)
    
    # 行内代码
    html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
    
    # 无序列表
    html = re.sub(r'^\- (.*?)$', r'<li>\1</li>', html, flags=re.MULTILINE)
    html = re.sub(r'(<li>.*?</li>\n)+', r'<ul>\g<0></ul>', html)
    
    # 有序列表
    html = re.sub(r'^\d+\. (.*?)$', r'<li>\1</li>', html, flags=re.MULTILINE)
    
    # 链接
    html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)
    
    # 段落（双换行）
    paragraphs = html.split('\n\n')
    processed = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # 如果已经是块级元素，不包装
        if re.match(r'^<(h[1-6]|ul|ol|li|pre|blockquote|table)', p):
            processed.append(p)
        else:
            # 单换行转 <br>
            p = p.replace('\n', '<br/>')
            processed.append(f'<p>{p}</p>')
    
    html = '\n'.join(processed)
    
    # 添加基础样式
    styled_html = f'''<section style="font-size: 16px; line-height: 1.8; color: #333; font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', 'PingFang SC', 'Microsoft YaHei', sans-serif;">
{html}
</section>'''
    
    return styled_html


def get_cover_image():
    """获取封面图片 URL（使用 picsum 随机图片）"""
    # 使用 picsum.photos 作为封面图
    return "https://picsum.photos/900/383"


def upload_image_to_wechat(image_url: str) -> str:
    """上传图片到微信公众号素材库"""
    token = get_access_token()
    
    # 下载图片
    response = requests.get(image_url)
    image_data = response.content
    
    # 上传到微信
    url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type=image"
    files = {
        'media': ('cover.jpg', image_data, 'image/jpeg')
    }
    response = requests.post(url, files=files)
    data = response.json()
    
    if "media_id" in data:
        print(f"✓ 上传封面图成功: {data['media_id']}")
        return data["media_id"]
    else:
        raise Exception(f"上传图片失败: {data}")


def upload_article_to_draft(title: str, content: str, cover_media_id: str, digest: str = "", author: str = ""):
    """上传文章到微信公众号草稿箱"""
    token = get_access_token()
    
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
    
    article = {
        "articles": [
            {
                "title": title,
                "author": author,
                "digest": digest,
                "content": content,
                "thumb_media_id": cover_media_id,
                "need_open_comment": 1,
                "only_fans_can_comment": 0
            }
        ]
    }
    
    response = requests.post(url, json=article)
    data = response.json()
    
    if "media_id" in data:
        return data["media_id"]
    else:
        raise Exception(f"上传文章失败: {data}")


def extract_summary(content: str, max_length: int = 120) -> str:
    """从文章内容提取摘要"""
    # 移除 frontmatter
    content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
    # 移除标题标记
    content = re.sub(r'^#+\s+', '', content, flags=re.MULTILINE)
    # 移除代码块
    content = re.sub(r'```.*?```', '', content, flags=re.DOTALL)
    # 移除链接
    content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
    # 移除粗体/斜体
    content = re.sub(r'\*+([^\*]+)\*+', r'\1', content)
    # 移除多余空白
    content = re.sub(r'\s+', ' ', content)
    # 截断
    if len(content) > max_length:
        return content[:max_length] + "..."
    return content.strip()


def main():
    # 读取文章
    article_path = Path("/Users/fuqingrong/Documents/agent开发/qrclaw/drafts/别再裸用 Claude Code 了！这套 Skills 配置让开发效率直接起飞.md")
    with open(article_path, 'r', encoding='utf-8') as f:
        md_content = f.read()
    
    # 提取标题
    title_match = re.search(r'^title:\s*(.+)$', md_content, re.MULTILINE)
    title = title_match.group(1) if title_match else "Claude Code Skills 配置指南"
    
    # 提取摘要
    digest = extract_summary(md_content)
    
    # 转换 Markdown 为 HTML
    print("正在转换 Markdown...")
    html_content = markdown_to_wechat_html(md_content)
    
    # 获取封面图
    print("正在获取封面图...")
    cover_url = get_cover_image()
    cover_media_id = upload_image_to_wechat(cover_url)
    
    # 上传文章
    print("正在上传文章到草稿箱...")
    media_id = upload_article_to_draft(
        title=title,
        content=html_content,
        cover_media_id=cover_media_id,
        digest=digest,
        author="小a玩ai助理"
    )
    
    print(f"\n✅ 发布完成！")
    print(f"• 标题：{title}")
    print(f"• 摘要：{digest[:50]}...")
    print(f"• media_id：{media_id}")
    print(f"\n草稿管理：https://mp.weixin.qq.com（内容管理 → 草稿箱）")


if __name__ == "__main__":
    main()