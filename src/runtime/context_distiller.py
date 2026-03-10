"""Context distiller agent for policy history compression."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from .prompt_manager import PromptManager


@dataclass
class DistilledContext:
    summary_markdown: str
    confirmed_facts: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    do_not_repeat: List[str] = field(default_factory=list)
    next_actions: List[str] = field(default_factory=list)


class ContextDistillerAgent:
    """LLM-only context distiller with a pseudo submit tool."""

    def __init__(self, llm: Any, prompt_manager: PromptManager):
        self.llm = llm
        self.prompt_manager = prompt_manager

    @staticmethod
    def _clean_lines(text: str) -> List[str]:
        out: List[str] = []
        seen = set()
        for line in str(text or "").splitlines():
            row = line.strip()
            if not row:
                continue
            if row.startswith("- "):
                row = row[2:].strip()
            if row in seen:
                continue
            seen.add(row)
            out.append(row)
        return out

    @staticmethod
    def _section(title: str, body: str) -> str:
        text = str(body or "").strip() or "无"
        return f"## {title}\n{text}"

    async def distill(
        self,
        *,
        user_request: str,
        iteration: int,
        task_board_md: str,
        knowledge_md: str,
        context_md: str,
        history_md: str,
    ) -> DistilledContext:
        @tool("submit_context_distillation", parse_docstring=True, error_on_invalid_docstring=True)
        def submit_context_distillation(
            primary_request_intent: str = "",
            key_technical_concepts: str = "",
            files_code_sections: str = "",
            problem_solving: str = "",
            pending_tasks: str = "",
            current_work: str = "",
            optional_next_step: str = "",
            direct_quotes_handoff: str = "",
            confirmed_facts: str = "",
            evidence: str = "",
            do_not_repeat: str = "",
            next_actions: str = "",
        ) -> str:
            """Submit the distilled 8-block context payload.

            Args:
                primary_request_intent: 请求主目标与成功标准。
                key_technical_concepts: 核心技术概念与术语。
                files_code_sections: 关键文件/函数/代码段定位信息。
                problem_solving: 已完成的问题求解过程摘要。
                pending_tasks: 尚未完成的任务列表。
                current_work: 当前进行中的工作状态。
                optional_next_step: 下一步建议动作。
                direct_quotes_handoff: 交接锚点或关键原文摘录。
                confirmed_facts: 可确认事实清单。
                evidence: 证据清单。
                do_not_repeat: 不应重复的动作或路径。
                next_actions: 后续推荐动作清单。

            Returns:
                纯文本确认信息，前缀为 OK:。
            """
            return "OK: context distillation submitted"

        system_prompt = self.prompt_manager.render("distiller/system.md", {})
        user_prompt = self.prompt_manager.render(
            "distiller/user.md",
            {
                "iteration": int(iteration),
                "user_request": str(user_request or ""),
                "task_board_md": str(task_board_md or ""),
                "knowledge_md": str(knowledge_md or ""),
                "context_md": str(context_md or ""),
                "history_md": str(history_md or ""),
            },
        )

        runnable = self.llm.bind_tools([submit_context_distillation])
        response = await runnable.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        tool_calls = getattr(response, "tool_calls", None)

        picked: Dict[str, Any] = {}
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                if str(call.get("name", "") or "").strip() != "submit_context_distillation":
                    continue
                args = call.get("args", {})
                if isinstance(args, dict):
                    picked = args
                break

        if not picked:
            fallback_text = str(getattr(response, "content", "") or "").strip()
            if not fallback_text:
                fallback_text = "无"
            summary = "\n\n".join(
                [
                    self._section("1) Primary Request and Intent", fallback_text),
                    self._section("2) Key Technical Concepts", "无"),
                    self._section("3) Files and Code Sections", "无"),
                    self._section("4) Problem Solving", "无"),
                    self._section("5) Pending Tasks", "无"),
                    self._section("6) Current Work", "无"),
                    self._section("7) Optional Next Step", "无"),
                    self._section("8) Direct Quotes / Handoff Anchors", "无"),
                ]
            )
            return DistilledContext(summary_markdown=summary)

        summary = "\n\n".join(
            [
                self._section("1) Primary Request and Intent", str(picked.get("primary_request_intent", "") or "")),
                self._section("2) Key Technical Concepts", str(picked.get("key_technical_concepts", "") or "")),
                self._section("3) Files and Code Sections", str(picked.get("files_code_sections", "") or "")),
                self._section("4) Problem Solving", str(picked.get("problem_solving", "") or "")),
                self._section("5) Pending Tasks", str(picked.get("pending_tasks", "") or "")),
                self._section("6) Current Work", str(picked.get("current_work", "") or "")),
                self._section("7) Optional Next Step", str(picked.get("optional_next_step", "") or "")),
                self._section("8) Direct Quotes / Handoff Anchors", str(picked.get("direct_quotes_handoff", "") or "")),
            ]
        )
        return DistilledContext(
            summary_markdown=summary,
            confirmed_facts=self._clean_lines(str(picked.get("confirmed_facts", "") or "")),
            evidence=self._clean_lines(str(picked.get("evidence", "") or "")),
            do_not_repeat=self._clean_lines(str(picked.get("do_not_repeat", "") or "")),
            next_actions=self._clean_lines(str(picked.get("next_actions", "") or "")),
        )
