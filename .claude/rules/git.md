# Git 规范

## Commit Message 格式

```
<type>: <简短描述> (#<issue-number>)
```

### Type

| Type | 说明 |
|---|---|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `refactor` | 重构（不改变行为） |
| `test` | 测试相关 |
| `docs` | 文档/规范 |
| `chore` | 构建/配置/依赖 |

### 规则

- 描述用英文，小写开头，不超过 72 字符
- 不需要句号结尾
- 需要关联 issue 时带 `(#N)`
- `Co-Authored-By` 行由 Claude Code 自动添加

### 示例

```
feat: EDPAgent inference backend for self-evolution (#002)
fix: use CLI model for dataset generation, validate full pipeline
refactor: rebrand to Hermes Agent Self-Evolution
```

## Branch 命名

- 功能分支：`feat/<描述>` 或 `<username>/<描述>`
- 直接从 `main` 分叉，通过 PR 合并回去
- 不做 force push 到 main

## PR 规范

- 标题用 commit message 格式
- Body 包含：Summary（1-3 条要点）、Test plan（checklist）
- 尾部加 `🤖 Generated with [Claude Code](https://claude.com/claude-code)`

## 不要做的事

- 不要 `git add -A` 或 `git add .`（避免意外提交 `.env`、密钥等）
- 不要 `git push --force` 到 main
- 不要跳过 hooks（`--no-verify`、`--no-gpg-sign`）
- 不要 `git commit --amend` 已推送的 commit
- 不要 `git rebase -i`（需要交互式输入）
- 不要用 `git commit --no-edit`
