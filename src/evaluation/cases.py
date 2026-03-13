"""Static case registry for full-system evaluation."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _p(*parts: str) -> str:
    return str((PROJECT_ROOT.joinpath(*parts)).resolve())


def _spec(name: str) -> str:
    return _p("reference", "docs", "eval_case_specs", name)


CASES: List[Dict[str, Any]] = [
    {
        "case_id": "preflight_struct_complex_test",
        "input_path": _p("test_binaries", "complex_test"),
        "source_path": _p("test_binaries", "complex_test.c"),
        "spec_path": _spec("struct_complex_test.md"),
        "request_text": "请分析目标程序并恢复关键结构体，输出恢复结果与改动说明。",
        "focus_points": [
            "恢复 Container、Node、FieldInfo 等核心结构体布局",
            "识别链表节点关系与字段数组",
            "将结构体定义应用到关键函数并沉淀注释",
        ],
        "evidence_functions": ["main", "sub_1780", "sub_15F0", "sub_16C0"],
        "profile": "struct_recovery",
        "expected_output_kind": "struct_definitions",
        "max_iterations": 6,
        "max_runtime_sec": 900,
        "allow_early_stop": True,
    },
    {
        "case_id": "preflight_attack_complex_test",
        "input_path": _p("test_binaries", "complex_test"),
        "source_path": _p("test_binaries", "complex_test.c"),
        "spec_path": _spec("attack_complex_test.md"),
        "request_text": "请分析目标程序的攻击面、输入入口与危险调用链，输出可执行风险摘要。",
        "focus_points": [
            "定位 main、create_node、validate_structure、decrypt_string 等入口/敏感路径",
            "关注外部输入、内存分配、回调执行、链表遍历",
            "输出入口点、风险路径和可控参数",
        ],
        "evidence_functions": ["main", "sub_1780", "sub_15F0", "sub_16C0", "sub_15B0"],
        "profile": "attack_surface",
        "expected_output_kind": "attack_surface_report",
        "max_iterations": 2,
        "max_runtime_sec": 900,
        "allow_early_stop": True,
    },
    {
        "case_id": "preflight_reverse_complex_test",
        "input_path": _p("test_binaries", "complex_test"),
        "source_path": _p("test_binaries", "complex_test.c"),
        "spec_path": _spec("reverse_complex_test.md"),
        "request_text": "请分析目标程序的关键逻辑、调用关系与数据流，输出逆向摘要。",
        "focus_points": [
            "总结 main 到 process_all_nodes/validate_structure 的主流程",
            "描述链表、字段数组、哈希函数与字符串解密的关系",
            "输出关键函数与数据流",
        ],
        "evidence_functions": ["main", "sub_1780", "sub_15F0", "sub_16C0", "sub_15B0"],
        "profile": "general_reverse",
        "expected_output_kind": "reverse_summary",
        "max_iterations": 2,
        "max_runtime_sec": 900,
        "allow_early_stop": True,
    },
    {
        "case_id": "struct_complex_test",
        "input_path": _p("test_binaries", "complex_test"),
        "source_path": _p("test_binaries", "complex_test.c"),
        "spec_path": _spec("struct_complex_test.md"),
        "request_text": "请分析目标程序并恢复关键结构体，输出恢复结果与改动说明。",
        "focus_points": [
            "恢复 Container、Node、Metadata、FieldInfo 的布局",
            "覆盖 create_node、process_all_nodes、validate_structure 证据链",
            "将结构体恢复结果写回 IDB 并输出注释摘要",
        ],
        "evidence_functions": ["main", "sub_1780", "sub_15F0", "sub_16C0"],
        "profile": "struct_recovery",
        "expected_output_kind": "struct_definitions",
        "max_iterations": 24,
        "max_runtime_sec": 1800,
        "allow_early_stop": False,
    },
    {
        "case_id": "struct_c_tagged_union_fsm",
        "input_path": _p("test_binaries", "suite_v2", "bin", "c_tagged_union_fsm_strip"),
        "source_path": _p("test_binaries", "suite_v2", "src", "c_tagged_union_fsm.c"),
        "spec_path": _spec("struct_c_tagged_union_fsm.md"),
        "request_text": "请分析目标程序并恢复关键结构体，重点恢复 tagged union 状态机中的消息与上下文结构体。",
        "focus_points": [
            "恢复 Message（MsgHeader + MsgPayload union）与 FsmContext",
            "跟踪 dispatch_msg 的 tag 分派到 handle_connect/handle_data/handle_close",
            "验证同一 union 内存在不同 handler 中被解释为不同 payload 类型",
        ],
        "evidence_functions": ["main", "dispatch_msg", "handle_connect", "handle_data", "handle_close", "process_queue"],
        "profile": "struct_recovery",
        "expected_output_kind": "struct_definitions",
        "max_iterations": 24,
        "max_runtime_sec": 1800,
        "allow_early_stop": False,
    },
    {
        "case_id": "struct_c_intrusive_list_registry",
        "input_path": _p("test_binaries", "suite_v2", "bin", "c_intrusive_list_registry_strip"),
        "source_path": _p("test_binaries", "suite_v2", "src", "c_intrusive_list_registry.c"),
        "spec_path": _spec("struct_c_intrusive_list_registry.md"),
        "request_text": "请分析目标程序并恢复关键结构体，重点恢复侵入式链表中 container_of 反推的父结构体。",
        "focus_points": [
            "恢复 TaskEntry、TimerEntry 与 ListNode",
            "识别 container_of 宏从 ListNode* 反推父结构体的指针算术",
            "验证 process_queue 和 dump_timers 中不同父结构体的字段访问",
        ],
        "evidence_functions": ["main", "register_task", "schedule_timer", "process_queue", "migrate_entry", "dump_timers"],
        "profile": "struct_recovery",
        "expected_output_kind": "struct_definitions",
        "max_iterations": 24,
        "max_runtime_sec": 1800,
        "allow_early_stop": False,
    },
    {
        "case_id": "struct_cpp_crtp_event_chain",
        "input_path": _p("test_binaries", "suite_v2", "bin", "cpp_crtp_event_chain_strip"),
        "source_path": _p("test_binaries", "suite_v2", "src", "cpp_crtp_event_chain.cpp"),
        "spec_path": _spec("struct_cpp_crtp_event_chain.md"),
        "request_text": "请分析目标程序并恢复关键结构体，重点恢复 CRTP 静态多态下的事件对象布局。",
        "focus_points": [
            "恢复 ConnEvent、DataEvent、ErrorEvent 与 ChainContext",
            "跟踪 CRTP 模板实例化后的 process/do_process 调用链",
            "验证 dispatch_all 中 void* 到具体事件类型的转换",
        ],
        "evidence_functions": ["main", "dispatch_all", "do_process", "process"],
        "profile": "struct_recovery",
        "expected_output_kind": "struct_definitions",
        "max_iterations": 24,
        "max_runtime_sec": 1800,
        "allow_early_stop": False,
    },
    {
        "case_id": "attack_complex_test",
        "input_path": _p("test_binaries", "complex_test"),
        "source_path": _p("test_binaries", "complex_test.c"),
        "spec_path": _spec("attack_complex_test.md"),
        "request_text": "请分析目标程序的攻击面、输入入口与危险调用链，输出可执行风险摘要。",
        "focus_points": [
            "main 是主要入口，follow create_node/process_all_nodes/validate_structure",
            "关注 decrypt_string、hash_djb2、calloc/free、回调函数",
            "总结潜在越界、空指针、长度/偏移相关风险",
        ],
        "evidence_functions": ["main", "sub_1780", "sub_15F0", "sub_16C0", "sub_15B0"],
        "profile": "attack_surface",
        "expected_output_kind": "attack_surface_report",
        "max_iterations": 18,
        "max_runtime_sec": 1500,
        "allow_early_stop": True,
    },
    {
        "case_id": "attack_c_tagged_union_fsm",
        "input_path": _p("test_binaries", "suite_v2", "bin", "c_tagged_union_fsm_strip"),
        "source_path": _p("test_binaries", "suite_v2", "src", "c_tagged_union_fsm.c"),
        "spec_path": _spec("attack_c_tagged_union_fsm.md"),
        "request_text": "请分析目标程序的攻击面、输入入口与危险调用链，输出可执行风险摘要。",
        "focus_points": [
            "分析 dispatch_msg 的 tag 分派逻辑与 union 类型混淆风险",
            "关注全局消息队列 g_msg_queue 的边界检查与溢出可能",
            "识别 handle_data 中 checksum 校验与 data 数组越界风险",
        ],
        "evidence_functions": ["main", "enqueue_msg", "dispatch_msg", "handle_connect", "handle_data", "handle_close"],
        "profile": "attack_surface",
        "expected_output_kind": "attack_surface_report",
        "max_iterations": 18,
        "max_runtime_sec": 1500,
        "allow_early_stop": True,
    },
    {
        "case_id": "reverse_complex_test",
        "input_path": _p("test_binaries", "complex_test"),
        "source_path": _p("test_binaries", "complex_test.c"),
        "spec_path": _spec("reverse_complex_test.md"),
        "request_text": "请分析目标程序的关键逻辑、调用关系与数据流，输出逆向摘要。",
        "focus_points": [
            "概括容器初始化、节点创建、节点处理、校验和释放链路",
            "描述字符串解密、哈希和全局计数器的作用",
            "列出关键函数与数据流",
        ],
        "evidence_functions": ["main", "sub_1780", "sub_15F0", "sub_16C0", "sub_15B0"],
        "profile": "general_reverse",
        "expected_output_kind": "reverse_summary",
        "max_iterations": 18,
        "max_runtime_sec": 1500,
        "allow_early_stop": True,
    },
    {
        "case_id": "reverse_cpp_multi_inherit_cast",
        "input_path": _p("test_binaries", "suite_v2", "bin", "cpp_multi_inherit_cast_strip"),
        "source_path": _p("test_binaries", "suite_v2", "src", "cpp_multi_inherit_cast.cpp"),
        "spec_path": _spec("reverse_cpp_multi_inherit_cast.md"),
        "request_text": "请分析目标程序的关键逻辑、调用关系与数据流，输出逆向摘要。",
        "focus_points": [
            "总结 Stream 多重继承 Readable/Writable/Seekable 的对象布局",
            "描述 do_read/do_write/do_seek 通过不同基类指针操作同一对象的 this 偏移",
            "概括 transfer 函数的数据流与 print_stream_state 的完整视图",
        ],
        "evidence_functions": ["main", "do_read", "do_write", "do_seek", "transfer", "print_stream_state"],
        "profile": "general_reverse",
        "expected_output_kind": "reverse_summary",
        "max_iterations": 18,
        "max_runtime_sec": 1500,
        "allow_early_stop": True,
    },
    {
        "case_id": "struct_c_multi_phase_builder",
        "input_path": _p("test_binaries", "suite_v2", "bin", "c_multi_phase_builder_strip"),
        "source_path": _p("test_binaries", "suite_v2", "src", "c_multi_phase_builder.c"),
        "spec_path": _spec("struct_c_multi_phase_builder.md"),
        "request_text": "请分析目标程序并恢复关键结构体，重点恢复多阶段初始化与 opaque handle 下的大结构体。",
        "focus_points": [
            "恢复 PipelineConfig（20+ 字段）",
            "组合 phase1_alloc/phase2_configure/phase3_bind_io/phase4_activate 的字段证据",
            "验证 handle 间接层（slab_get）不阻碍类型传播",
        ],
        "evidence_functions": ["main", "phase1_alloc", "phase2_configure", "phase3_bind_io", "phase4_activate", "run_pipeline"],
        "profile": "struct_recovery",
        "expected_output_kind": "struct_definitions",
        "max_iterations": 24,
        "max_runtime_sec": 1800,
        "allow_early_stop": False,
    },
    {
        "case_id": "struct_cpp_pimpl_bridge",
        "input_path": _p("test_binaries", "suite_v2", "bin", "cpp_pimpl_bridge_strip"),
        "source_path": _p("test_binaries", "suite_v2", "src", "cpp_pimpl_bridge.cpp"),
        "spec_path": _spec("struct_cpp_pimpl_bridge.md"),
        "request_text": "请分析目标程序并恢复关键结构体，重点恢复 Pimpl 模式下隐藏的实现结构体。",
        "focus_points": [
            "恢复 Connection::Impl（含嵌套 RingBuffer 和 Metrics）",
            "穿透 Connection 公开方法到 Impl 方法的 forwarding 层",
            "验证 do_send/do_recv 中 RingBuffer 子结构体的字段访问",
        ],
        "evidence_functions": ["main", "connect", "send", "recv", "close", "do_connect", "do_send", "do_recv", "dump_stats"],
        "profile": "struct_recovery",
        "expected_output_kind": "struct_definitions",
        "max_iterations": 24,
        "max_runtime_sec": 1800,
        "allow_early_stop": False,
    },
    {
        "case_id": "struct_cpp_multi_inherit_cast",
        "input_path": _p("test_binaries", "suite_v2", "bin", "cpp_multi_inherit_cast_strip"),
        "source_path": _p("test_binaries", "suite_v2", "src", "cpp_multi_inherit_cast.cpp"),
        "spec_path": _spec("struct_cpp_multi_inherit_cast.md"),
        "request_text": "请分析目标程序并恢复关键结构体，重点恢复多重继承下不同基类指针偏移的完整对象布局。",
        "focus_points": [
            "恢复 Stream（继承 Readable、Writable、Seekable）",
            "跟踪 do_read/do_write/do_seek 中不同基类指针的 this 偏移调整",
            "验证 transfer 和 print_stream_state 中完整对象视图",
        ],
        "evidence_functions": ["main", "do_read", "do_write", "do_seek", "transfer", "print_stream_state"],
        "profile": "struct_recovery",
        "expected_output_kind": "struct_definitions",
        "max_iterations": 24,
        "max_runtime_sec": 1800,
        "allow_early_stop": False,
    },
]


SUITES: Dict[str, List[str]] = {
    "preflight": [
        "preflight_struct_complex_test",
        "preflight_attack_complex_test",
        "preflight_reverse_complex_test",
    ],
    "core": [
        "struct_complex_test",
        "struct_c_tagged_union_fsm",
        "struct_c_intrusive_list_registry",
        "struct_cpp_crtp_event_chain",
        "attack_complex_test",
        "attack_c_tagged_union_fsm",
        "reverse_complex_test",
        "reverse_cpp_multi_inherit_cast",
    ],
    "full": [
        "struct_complex_test",
        "struct_c_tagged_union_fsm",
        "struct_c_intrusive_list_registry",
        "struct_cpp_crtp_event_chain",
        "struct_c_multi_phase_builder",
        "struct_cpp_pimpl_bridge",
        "struct_cpp_multi_inherit_cast",
        "attack_complex_test",
        "attack_c_tagged_union_fsm",
        "reverse_complex_test",
        "reverse_cpp_multi_inherit_cast",
    ],
}


CASE_BY_ID: Dict[str, Dict[str, Any]] = {row["case_id"]: dict(row) for row in CASES}


def get_case(case_id: str) -> Dict[str, Any]:
    key = str(case_id or "").strip()
    if key not in CASE_BY_ID:
        raise KeyError(f"unknown case_id: {case_id}")
    return dict(CASE_BY_ID[key])


def list_cases() -> List[Dict[str, Any]]:
    return [dict(row) for row in CASES]


def get_suite_case_ids(suite: str) -> List[str]:
    name = str(suite or "").strip()
    if name not in SUITES:
        raise KeyError(f"unknown suite: {suite}")
    return list(SUITES[name])


def get_suite_cases(suite: str) -> List[Dict[str, Any]]:
    return [get_case(case_id) for case_id in get_suite_case_ids(suite)]


def resolve_cases(*, suite: str = "", case_ids: List[str] | None = None) -> List[Dict[str, Any]]:
    picked: List[str] = []
    if str(suite or "").strip():
        picked.extend(get_suite_case_ids(suite))
    for case_id in case_ids or []:
        value = str(case_id or "").strip()
        if value:
            picked.append(value)
    seen = set()
    ordered: List[str] = []
    for case_id in picked:
        if case_id in seen:
            continue
        seen.add(case_id)
        ordered.append(case_id)
    if not ordered:
        raise ValueError("no suite or case specified")
    return [get_case(case_id) for case_id in ordered]
