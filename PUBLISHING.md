# QRClaw 发布指南

## 发布方式

### 1. PyPI（推荐）

#### 准备工作

1. 创建 PyPI 账号
   - 访问 https://pypi.org/account/register/
   - 注册账号并验证邮箱

2. 创建 API Token
   - 访问 https://pypi.org/manage/account/token/
   - 创建 API Token（保存好，只显示一次）

3. 配置 `~/.pypirc`
   ```ini
   [pypi]
     username = __token__
     password = pypi-xxx...  # 你的 API Token
   ```

#### 发布步骤

1. 完善 `pyproject.toml`（见下方配置）

2. 构建包
   ```bash
   pip install build
   python -m build
   ```

3. 上传到 TestPyPI（测试）
   ```bash
   pip install twine
   twine upload --repository testpypi dist/*

   # 测试安装
   pip install --index-url https://test.pypi.org/simple/ qrclaw
   ```

4. 上传到 PyPI（正式）
   ```bash
   twine upload dist/*
   ```

5. 用户安装
   ```bash
   pip install qrclaw
   qrclaw
   ```

#### pyproject.toml 配置

```toml
[project]
name = "qrclaw"
version = "0.1.0"
description = "A powerful AI agent with skills system"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.11"
authors = [
    {name = "Your Name", email = "your.email@example.com"}
]
keywords = ["ai", "agent", "cli", "skills"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]
dependencies = [
    "openai>=1.0.0",
    "python-dotenv>=1.0.0",
    "rich>=13.0.0",
    "pydantic>=2.0.0",
    "tavily-python>=0.7.0",
    "httpx>=0.27.0",
    "prompt_toolkit>=3.0.0",
    "pyyaml>=6.0.0",
]

[project.scripts]
qrclaw = "qrclaw.cli:main"

[project.urls]
Homepage = "https://github.com/fu-qingrong/qrclaw"
Repository = "https://github.com/fu-qingrong/qrclaw"
Issues = "https://github.com/fu-qingrong/qrclaw/issues"

[tool.hatch.build.targets.wheel]
packages = ["qrclaw"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### 2. GitHub Releases

#### 自动化发布（GitHub Actions）

创建 `.github/workflows/publish.yml`：

```yaml
name: Publish to PyPI

on:
  release:
    types: [published]

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install build twine

      - name: Build
        run: python -m build

      - name: Publish to PyPI
        env:
          TWINE_USERNAME: __token__
          TWINE_PASSWORD: ${{ secrets.PYPI_API_TOKEN }}
        run: twine upload dist/*
```

#### 发布流程

1. 创建 GitHub Release
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

2. GitHub 自动构建并发布到 PyPI

### 3. Homebrew（macOS 用户）

#### 自定义 Tap

1. 创建 Homebrew Formula 仓库
   ```
   github.com/fu-qingrong/homebrew-qrclaw
   ```

2. 创建 Formula 文件 `Formula/qrclaw.rb`
   ```ruby
   class Qrclaw < Formula
     desc "A powerful AI agent with skills system"
     homepage "https://github.com/fu-qingrong/qrclaw"
     url "https://files.pythonhosted.org/packages/source/q/qrclaw/qrclaw-0.1.0.tar.gz"
     sha256 "xxx"  # 计算方法：shasum -a 256 qrclaw-0.1.0.tar.gz
     license "MIT"

     depends_on "python@3.11"

     def install
       system "pip", "install", ".", *std_pip_args
       bin.install_symlink libexec/"bin/qrclaw"
     end

     test do
       system "#{bin}/qrclaw", "--version"
     end
   end
   ```

3. 用户安装
   ```bash
   brew tap fu-qingrong/qrclaw
   brew install qrclaw
   ```

### 4. Docker

#### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 安装 qrclaw
RUN pip install -e .

# 创建数据目录
RUN mkdir -p /root/.qrclaw

ENTRYPOINT ["qrclaw"]
```

#### 构建和发布

```bash
# 构建
docker build -t qrclaw:latest .

# 发布到 Docker Hub
docker tag qrclaw:latest fuqingrong/qrclaw:latest
docker push fuqingrong/qrclaw:latest

# 用户使用
docker run -it fuqingrong/qrclaw
```

### 5. 一键安装脚本

#### install.sh

```bash
#!/bin/bash
set -e

echo "🚀 安装 QRClaw..."

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 Python 3，请先安装 Python 3.11+"
    exit 1
fi

# 检查 pip
if ! command -v pip3 &> /dev/null; then
    echo "❌ 未找到 pip3，请先安装 pip"
    exit 1
fi

# 安装 qrclaw
echo "📦 正在安装 qrclaw..."
pip3 install qrclaw

# 验证安装
if command -v qrclaw &> /dev/null; then
    echo "✅ 安装成功！"
    echo ""
    echo "运行 'qrclaw' 开始使用"
    echo "运行 'qrclaw --help' 查看帮助"
else
    echo "❌ 安装失败，请检查日志"
    exit 1
fi
```

#### 用户使用

```bash
curl -sSL https://raw.githubusercontent.com/fu-qingrong/qrclaw/main/install.sh | bash
```

## 发布检查清单

### 必需文件

- [x] `pyproject.toml` - 包配置
- [ ] `README.md` - 项目说明
- [ ] `LICENSE` - 开源许可证
- [ ] `CHANGELOG.md` - 版本更新日志
- [ ] `.env.example` - 环境变量示例

### 必需内容

- [ ] 项目描述
- [ ] 安装说明
- [ ] 使用示例
- [ ] API 文档
- [ ] 贡献指南

### 版本管理

- [ ] 使用语义化版本：v0.1.0
- [ ] 维护 CHANGELOG.md
- [ ] Git 标签对应版本

## 发布时间线

### 第 1 周：准备工作
- 完善 README.md
- 添加 LICENSE
- 完善 pyproject.toml
- 测试安装流程

### 第 2 周：发布 PyPI
- 发布到 TestPyPI 测试
- 发布到 PyPI 正式
- 更新 GitHub Release

### 第 3 周：扩展渠道
- 创建 Homebrew Formula
- 发布 Docker 镜像
- 创建一键安装脚本

### 第 4 周：文档和推广
- 完善文档
- 发布教程
- 社区推广

## 用户安装方式汇总

```bash
# 方式 1: PyPI（推荐）
pip install qrclaw

# 方式 2: GitHub
pip install git+https://github.com/fu-qingrong/qrclaw.git

# 方式 3: Homebrew（macOS）
brew tap fu-qingrong/qrclaw
brew install qrclaw

# 方式 4: Docker
docker run -it fuqingrong/qrclaw

# 方式 5: 一键安装
curl -sSL https://raw.githubusercontent.com/fu-qingrong/qrclaw/main/install.sh | bash
```