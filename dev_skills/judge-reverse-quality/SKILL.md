---
name: judge-reverse-quality
description: 在 run 已结束、需要判断修改是变好持平还是变差、需要根据纯文本证据复判，或补跑 judge-only 时使用。
user-invocable: true
tags: [judge, evaluation, reverse, quality]
---

# judge-reverse-quality

## 触发条件

- run 已结束，需要判断这次修改是变好、持平还是变差
- 需要根据纯文本证据复判
- 需要补跑 `--judge-only`

## 使用步骤

1. 先读取当前 case 的：
   - `evidence.md`
   - `run_trace.md`
   - `verdict.md`
   - `.eval_state/watch.log`
   - `.eval_state/stderr.log`
2. 若有 baseline，再对照 baseline 的同类文件
3. 同时回看源码和 `focus_points`，避免只看摘要
4. 按 profile 判断：
   - `struct_recovery`：布局、字段、类型传播、注释沉淀
   - `attack_surface`：入口、危险链、参数控制点、证据链完整性
   - `general_reverse`：行为摘要、数据流、调用链归纳、结论一致性
5. 最终输出必须采用伪 tool 结构：

```text
submit_eval_verdict(
  verdict=pass|partial|fail|infra_fail,
  summary=一句话结论,
  evidence=2-4条关键证据,
  risks=残留风险或缺口
)
```

## 命令入口

```bash
cd /mnt/d/reverse/agentic_ida_pro
export PYTHONPATH=src

/mnt/d/reverse/agentic_ida_pro/.venv/bin/python -u src/entrypoints/eval_runner.py \
  --judge-only logs/eval_runs/<run_id>
```
