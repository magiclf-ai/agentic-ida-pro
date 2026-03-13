---
name: author-regression-case
description: 在一次真实失败值得长期回归保护、需要把线上或手工调试问题沉淀成新 case 并接入 suite 时使用。
user-invocable: true
tags: [regression, case, evaluation, suite]
---

# author-regression-case

## 触发条件

- 真实失败模式值得长期回归保护
- 需要把一次线上或手工调试失败固化为 case

## 使用步骤

1. 先确认失败属于哪个 profile：
   - `struct_recovery`
   - `attack_surface`
   - `general_reverse`
2. 放置样本：
   - 二进制或 IDB 放到 `test_binaries/`
   - 源码放到对应 `src/` 或配套目录
3. 为新样本定义：
   - `case_id`
   - `request_text`
   - `focus_points`
   - `expected_output_kind`
4. 在 `src/evaluation/cases.py` 注册 case，并加入合适 suite
5. 验证注册成功：

```bash
/mnt/d/reverse/agentic_ida_pro/.venv/bin/python -u src/entrypoints/eval_runner.py --case-info <case_id>
```

6. 若是 `struct_recovery`，尽量保证源码里有可解析的结构体定义
7. 若是 `attack_surface` / `general_reverse`，优先补 `@ground_truth:*` 注释；暂时没有时至少保证 `focus_points` 清晰
8. 最后用单 case 运行一次：

```bash
cd /mnt/d/reverse/agentic_ida_pro
export PYTHONPATH=src
/mnt/d/reverse/agentic_ida_pro/.venv/bin/python -u src/entrypoints/eval_runner.py --case <case_id>
```

## 交付要求

- 新 case 能被 `eval_runner.py --case <case_id>` 识别
- 至少说明该 case 防止的退化模式
- case 说明与计划/实现文档放到 `reference/docs/`
