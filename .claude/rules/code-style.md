# 代码规范

## 文件头

- `evolution/` 模块：文件头用简短英文描述
- `edp_agent/` 模块：文件头用中文 docstring 描述公开接口
- 使用 `from __future__ import annotations` 以支持 forward reference（`edp_agent/` 已统一使用）

## 命名

| 元素 | 规范 | 示例 |
|---|---|---|
| 模块/文件 | snake_case | `skill_module.py`, `dataset_builder.py` |
| 类 | PascalCase | `EvolutionConfig`, `SkillEvolutionAdapter` |
| 函数/方法 | snake_case | `load_skill()`, `run_edp_agent()` |
| 私有函数 | `_` 前缀 | `_parse_score()`, `_get_edp_agent_path()` |
| 模块级私有变量 | `_` 前缀 | `_edp_initialized`, `_trajectories` |
| 常量 | UPPER_SNAKE | `_AGENT_RULE_PATH` (模块级) |

## Import 顺序

1. `from __future__ import annotations`（如使用）
2. 标准库
3. 第三方库
4. 本地模块

各组之间空一行。示例：

```python
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import dspy

from evolution.core.config import EvolutionConfig
from evolution.core.fitness import LLMJudge
```

## 类型注解

- 公开函数参数和返回值必须有类型注解
- 使用 `Optional[X]` 而非 `X | None`（保持 3.10 兼容）
- dataclass 字段必须标注类型
- 私有辅助函数可省略注解

## Docstring

- 公开函数：一行描述功能即可，不需要多行
- 不会在 PR 中增加额外 docstring 噪声 — 只写对读者非显而易见的约束或行为
- 私有函数不加 docstring

## 文件长度

- 单个文件不超过 500 行
- 超过则拆分为子模块

## 格式化

- 缩进：4 空格
- 行宽：建议 100 字符，不强求
- 无 trailing whitespace
- 文件末尾保留一个空行
