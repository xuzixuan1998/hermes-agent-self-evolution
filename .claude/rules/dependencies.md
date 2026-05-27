# 依赖管理

## 包管理器

使用 **uv** 管理 Python 依赖。

### 安装

```bash
# 创建虚拟环境 + 安装全部依赖
uv sync

# 仅安装生产依赖
uv sync --no-dev

# 添加新依赖
uv add <package-name>

# 添加开发依赖
uv add --dev <package-name>
```

### 锁定文件

- `uv.lock` 提交到 git（可复现构建）
- 更新依赖后运行 `uv lock --upgrade` 刷新锁定文件

### 运行测试

```bash
uv run pytest
```

## 依赖清单

### 核心依赖

| 包 | 版本 | 用途 |
|---|---|---|
| dspy | >=3.0.0 | LLM 编程框架，ChainOfThought / GEPA |
| gepa | ==0.1.1 | 进化优化引擎（generate-evaluate 循环） |
| click | >=8.0 | CLI 入口 |
| rich | >=13.0 | 终端输出格式化 |
| pyyaml | >=6.0 | YAML frontmatter 解析（edp_agent） |
| openai | >=1.0.0 | dspy 底层 LLM 调用（litellm 的 transitive dep） |

### 开发依赖

| 包 | 版本 | 用途 |
|---|---|---|
| pytest | >=7.0 | 测试框架 |
| pytest-asyncio | >=0.21 | async 测试支持 |

## 不需要 install 的外部依赖

以下依赖通过 `sys.path` 注入，不需要 pip install：

- **hermes-agent**: `AIAgent` 从 `--hermes-repo` 路径的 `run_agent.py` 动态导入
- **edp_agent/**: 已内嵌在本仓库中，不需要单独安装
