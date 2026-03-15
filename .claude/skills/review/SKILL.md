---
name: review
description: "Code review. Adversarial review — баги, security, медицинская точность, производительность. Запускает code-critic агента."
user-invocable: true
allowed-tools: Agent, Read, Grep, Glob
---

# Code Review

Запусти агента code-critic для adversarial review.

## Действия

1. Определи какие файлы изменены (git diff или указание пользователя)
2. Запусти Agent:
   - `subagent_type`: general-purpose
   - `model`: opus
   - Передай в prompt: файлы для ревью + правила из `.claude/agents/06-code-critic.md`

## Что проверяет

- **Баги**: None dereference, division by zero, index errors, type errors
- **OpenCV**: BGR/RGB путаница, uint8 overflow, camera release
- **tkinter**: PhotoImage GC, thread safety, after() vs sleep()
- **Security**: SQL injection, path traversal, пациентские данные в логах
- **Performance**: лишние копии, Python циклы вместо NumPy, N+1 queries
- **Медицина**: точность вычислений, единицы измерения, абсурдные значения

## Выходной формат

```
Quality Score: XX/100
Verdict: APPROVE | REQUEST_CHANGES | BLOCK
Issues: 🔴 Critical | 🟠 High | 🟡 Medium | ⚪ Low
```
