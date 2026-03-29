# QRClaw 沙箱隔离设计

> 基于 Docker 的物理隔离，配置简单，安全可靠。

---

## 一、核心原理

**Docker 容器隔离 = 物理隔离**

- Agent 只能访问挂载到容器内的目录
- 敏感路径（`/etc`, `/root`, `docker.sock` 等）禁止挂载
- 容器内执行 `rm -rf /` 只会删除容器内的文件，不影响主机

**不需要额外的路径检查、命令检查、权限检查**，因为容器已经物理隔离了。

---

## 二、配置文件

用户只需要一个配置文件：`~/.qrclaw/permissions.yaml`

```yaml
# QRClaw 权限与沙箱配置

default_policy: "restricted"

agents:
  # 主 Agent：完全信任，不启用沙箱
  default:
    access: "full"
    sandbox:
      enabled: false
  
  # 开发 Agent：启用沙箱
  developer:
    access: "scoped"
    sandbox:
      enabled: true
      image: "python:3.11-slim"
      network: "none"
      memory: "512m"
      mounts:
        - host: "~/projects/my-app"
          container: "/workspace"
          mode: "rw"
        - host: "/data/knowledge-base"
          container: "/knowledge"
          mode: "ro"
```

---

## 三、模块结构

```
qrclaw/
├── sandbox/
│   ├── __init__.py      # 模块入口
│   ├── config.py        # 配置管理（读取 permissions.yaml）
│   ├── container.py     # Docker 容器操作
│   ├── manager.py       # 沙箱管理器
│   └── validator.py     # 挂载路径验证
├── tools/
│   ├── shell.py         # run_shell（支持沙箱执行）
│   └── spawn_agent.py   # 子 Agent（自动创建沙箱）
└── workspace.py         # 工作空间（自动切换 cwd）
```

**删除了**：`security.py`（物理隔离后不需要软件层面的权限检查）

---

## 四、使用方式

### 4.1 配置 Agent

```yaml
# ~/.qrclaw/permissions.yaml

agents:
  my-agent:
    sandbox:
      enabled: true           # 启用沙箱
      image: "python:3.11-slim"  # Docker 镜像
      network: "none"         # 无网络（或 "bridge" 允许网络）
      memory: "512m"          # 内存限制
      pids_limit: 256         # 进程数限制
      mounts:                 # 挂载配置
        - host: "~/projects/my-app"
          container: "/workspace"
          mode: "rw"          # 可读写
        - host: "/data/docs"
          container: "/docs"
          mode: "ro"          # 只读
```

### 4.2 自动使用

配置好后，`run_shell` 会自动检测并使用沙箱：

```python
# 工具调用时自动检测
run_shell("ls /workspace")  # 如果启用沙箱，在容器内执行
```

子 Agent 也会自动创建沙箱：

```python
spawn_agent("developer", "分析项目代码")  # 自动创建沙箱
```

---

## 五、安全特性

| 特性 | Docker 参数 | 说明 |
|------|-------------|------|
| 只读根文件系统 | `--read-only` | 防止修改系统文件 |
| 移除能力 | `--cap-drop ALL` | 移除所有 Linux 能力 |
| 无网络 | `--network none` | 默认无网络访问 |
| 进程限制 | `--pids-limit 256` | 防止 fork bomb |
| 内存限制 | `--memory 512m` | 防止内存耗尽 |
| 禁止提权 | `--security-opt no-new-privileges` | 防止权限提升 |
| 挂载黑名单 | 代码验证 | 禁止挂载敏感路径 |

---

## 六、与 OpenClaw 对比

| 特性 | OpenClaw | QRClaw |
|------|----------|--------|
| 配置文件 | 2个（permissions + sandbox） | 1个（permissions） |
| 权限检查 | 软件层面 | 容器物理隔离 |
| 命令检查 | 黑名单 + LLM | 不需要（容器隔离） |
| 路径检查 | 软件层面 | 不需要（容器隔离） |
| 实现复杂度 | 高 | 低 |

**结论**：QRClaw 更简单，因为物理隔离天然解决了所有安全问题。