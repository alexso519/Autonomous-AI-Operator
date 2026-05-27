"""
Application locale — default Traditional Chinese (Hong Kong).

Used for agent prompts, execution telemetry, and user-facing backend messages.
Override with APP_LOCALE env var (e.g. en_US).
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_LOCALE = "zh_HK"

_LOCALE = os.getenv("APP_LOCALE", DEFAULT_LOCALE).strip() or DEFAULT_LOCALE


def get_locale() -> str:
    return _LOCALE


def bcp47_tag() -> str:
    """BCP 47 tag for Intl formatters (zh-HK, en-US, …)."""
    return _LOCALE.replace("_", "-")


def is_zh_hk() -> bool:
    return _LOCALE.lower().startswith("zh")


def language_instruction() -> str:
    if is_zh_hk():
        return (
            "請使用香港繁體中文（zh-HK）回覆。"
            "保持專業、簡潔、事實導向；保留 Markdown 結構與 [n] 引用格式。"
        )
    return "Respond in clear, concise English unless the user specifies another language."


def t(key: str, **kwargs: Any) -> str:
    template = _MESSAGES.get(_LOCALE, _MESSAGES[DEFAULT_LOCALE]).get(key)
    if template is None:
        template = _MESSAGES[DEFAULT_LOCALE].get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template


_MESSAGES: dict[str, dict[str, str]] = {
    "zh_HK": {
        "acting_as": "你正在扮演：{label}",
        "your_goal": "你的目標：{goal}",
        "context_from_agents": "--- 來先前代理的上下文 ---",
        "instructions": "--- 指示 ---",
        "complete_goal_with_evidence": "使用上述已收集的證據完成你的目標。",
        "complete_goal_with_context": "使用上述上下文完成你的目標。",
        "be_concise": "保持簡潔、結構清晰 — 避免重複及空泛內容。",
        "respond_markdown_sections": "以 Markdown 撰寫，包含：摘要、發現、建議、後續步驟。",
        "use_tools_not_guess": "涉及事實的內容須使用工具查證，切勿臆測。",
        "tool_guidance": "--- 工具指引 ---",
        "starting_workflow": "開始工作流程：{name}（{count} 個節點）",
        "activating_agent": "[{idx}/{total}] 啟動 {name}…",
        "starting_task": "開始任務：{goal}",
        "no_goal": "未指定目標",
        "context_budget": "上下文預算：約 {tokens} tokens",
        "agent_thinking": "⏳ {name} 思考中…",
        "agent_completed": "✓ {name} 已完成",
        "confidence": "信心 {score}：{reasoning}",
        "confidence_ok": "信心 {score} 在可接受範圍內。",
        "confidence_critical": "信心嚴重偏低 — 建議改用綜合報告。",
        "confidence_low_grounding": "信心偏低且存在事實風險 — 需要工具查證。",
        "confidence_incomplete": "輸出不完整 — 需要澄清重試。",
        "confidence_low_alternate": "信心偏低 — 建議採用替代推理路徑。",
        "quality_score": "品質評分 {score}：{detail}",
        "quality_acceptable": "品質評分 {score}：可接受",
        "quality_hedge_suppressed": "品質 {score} — 僅因保守用詞，本地模型不觸發重試",
        "recovery_suppressed": "復原節點不納入重新評估",
        "synthesizing": "正在綜合各代理輸出，生成最終報告…",
        "synthesis_conflicts": "已解決 {count} 個衝突觀點",
        "final_report_ready": "最終報告已就緒",
        "workflow_completed": "✓ 工作流程「{name}」已成功完成",
        "workflow_completed_reason": "工作流程已成功完成",
        "selected_strategy": "已選策略：{label}（評分 {score}）",
        "research_chain_started": "開始研究工具鏈：{name}",
        "source_ranked": "已排序：{title}（證據 {score}）",
        "evidence_collected": "證據來源 {domain}：{title}",
        "citation_attached": "引用 [{marker}] → {title}",
        "provenance_linked": "已連結事實至 {tool}：{fact}",
        "model_selected_tongyi": "已選通義模型：{model}",
        "model_forced": "前端設定強制使用模型：{model}",
        "tool_completed": "工具 {name} 已成功完成。",
        "tool_invoked": "已調用 {name}",
        "tool_result_ready": "工具 {name} 結果已就緒",
        "tool_failed": "工具 {name} 失敗：{error}",
        "tool_approval_required": "工具 {name} 需要批准後方可執行。",
        "execution_cancelled": "執行已在節點「{name}」取消",
        "agent_timeout": "代理執行逾時（{timeout} 秒）",
        "resolved_conflicts": "已解決 {count} 個衝突觀點",
        "follow_instructions": "請仔細遵循指示，提供簡潔、基於事實的輸出。",
        "no_reflection_issues": "未偵測到反思問題。",
        "decomposed_subgoals": "已將任務分解為 {count} 個子目標節點",
        "replanning_after": "於 {agent} 後重新規劃：{reason}",
        "graph_updated": "圖已更新：已插入 {count} 個恢復節點",
        "replanning_complete": "重新規劃完成 — 執行計劃共 {count} 個節點",
        "spawned_child_agent": "已為子目標生成子代理：{subgoal}",
        "pipeline_evidence_quality": "品質 {score} — 已有管道證據；跳過工具重試",
        "pipeline_evidence_no_tools": "品質 {score} — 已有管道證據；無需各節點工具調用",
        "quality_retry": "品質 {score} — {reason} → {retry_type}",
        "retry_blocked": "重試被阻止：{reason}。問題：{issues}",
        "reflection_limit": "已達 {agent} 的反思上限：{reason}",
        "injected_recovery": "品質 {score}（{agent}）。已注入 {retry_type} 恢復步驟。",
        "default_task": "完成指定任務。",
        "default_role": "助理",
        "approval_required": "需要批准",
        "proceed_workflow": "是否繼續執行工作流程？",
        "untitled_workflow": "未命名工作流程",
        "execution_not_found": "找不到執行紀錄",
        "workflow_not_found": "找不到工作流程",
        "workflow_deleted": "找不到工作流程 {id}（可能已被刪除）",
        "workflow_no_nodes": "工作流程沒有節點 — 無內容可執行",
        "workflow_no_agents": "工作流程沒有代理節點。請先在畫布上加入代理。",
        "lock_contention": "因鎖競爭無法開始執行",
        "lock_contention_rerun": "因鎖競爭無法重新執行",
        "analysis_failed": "分析失敗：{error}",
        "execution_failed": "執行啟動失敗：{error}",
        "provider_not_allowed": "不允許所選供應商",
        "api_key_not_configured": "尚未設定通義 API 金鑰",
        "model_not_allowed": "不允許所選模型",
        "temperature_range": "溫度必須介乎 0 至 2",
        "not_waiting_approval": "執行並非等待批准狀態（目前：{status}）",
        "not_waiting_approval_detail": "執行並非等待批准狀態（目前：{status}）。只有「等待批准」的執行才可操作。",
        "approval_in_progress": "此執行已有另一批准操作進行中",
        "status_changed_approval": "批准處理前執行狀態已變更",
        "status_changed_rejection": "拒絕處理前執行狀態已變更",
        "resume_failed": "恢復執行失敗：{error}",
        "reject_failed": "拒絕執行失敗：{error}",
        "execution_rejected": "執行已拒絕",
        "not_cancellable": "執行不可取消（目前狀態：{status}）",
        "not_in_registry": "執行不在執行環境註冊表中（可能剛完成）",
        "session_not_found": "找不到工作階段",
        "benchmark_not_found": "找不到基準測試任務 {id}",
        "spawn_plan_not_found": "找不到生成計劃",
        "no_replay_data": "此執行沒有重播數據",
        "approval_request_not_found": "找不到批准請求",
        "backstory_researcher": "你是嚴謹的研究分析員，負責收集事實、核實來源並呈報有證據支持的發現。",
        "backstory_writer": "你是熟練的內容策略師，將原始資料轉化為清晰、具說服力的敘述。",
        "backstory_reviewer": "你是徹底的品質保證主管，找出不一致之處、驗證邏輯並確保高標準。",
        "backstory_planner": "你是有條理的項目協調員，將複雜目標分解為結構化、可執行的步驟。",
        "backstory_coder": "你是資深軟件工程師，撰寫乾淨、有文件、正確的程式碼。",
        "backstory_analyst": "你是數據驅動的分析員，提取模式、識別趨勢並產出可行洞察。",
        "backstory_default": "你是 {role}。你的目標是：{goal}",
        "api_key_missing_error": "已選通義供應商但缺少 API 金鑰。請先在 LLM 設定中配置。",
    },
    "en_US": {
        "acting_as": "You are acting as: {label}",
        "your_goal": "Your goal: {goal}",
        "context_from_agents": "--- Context from previous agents ---",
        "instructions": "--- Instructions ---",
        "complete_goal_with_evidence": "Complete your goal using the pre-collected evidence above.",
        "complete_goal_with_context": "Complete your goal using the context provided above.",
        "be_concise": "Be concise and structured — avoid repetition and filler.",
        "respond_markdown_sections": "Respond in markdown prose with Summary, Findings, Recommendations, and Next Steps.",
        "use_tools_not_guess": "Use tools for factual claims instead of guessing.",
        "tool_guidance": "--- Tool Guidance ---",
        "starting_workflow": "Starting workflow: {name} ({count} nodes)",
        "activating_agent": "[{idx}/{total}] Activating {name}...",
        "starting_task": "Starting task: {goal}",
        "no_goal": "No goal specified",
        "context_budget": "Context budget: ~{tokens} tokens",
        "agent_thinking": "⏳ {name} is thinking...",
        "agent_completed": "✓ {name} completed",
        "confidence": "Confidence {score}: {reasoning}",
        "confidence_ok": "Confidence {score} within acceptable range.",
        "confidence_critical": "Critical confidence — synthesis fallback recommended.",
        "confidence_low_grounding": "Low confidence with factual risk — tool grounding.",
        "confidence_incomplete": "Incomplete output — clarification retry.",
        "confidence_low_alternate": "Low confidence — alternate reasoning path.",
        "quality_score": "Quality score {score}: {detail}",
        "quality_acceptable": "Quality score {score}: acceptable",
        "quality_hedge_suppressed": "Quality {score} — hedge wording alone does not trigger retry on local models",
        "recovery_suppressed": "Recovery nodes are excluded from re-evaluation",
        "synthesizing": "Synthesizing final report from agent outputs...",
        "synthesis_conflicts": "Resolved {count} conflicting perspective(s)",
        "final_report_ready": "Final report ready",
        "workflow_completed": "✓ Workflow '{name}' completed successfully",
        "workflow_completed_reason": "Workflow completed successfully",
        "selected_strategy": "Selected strategy: {label} (score {score})",
        "research_chain_started": "Starting research tool chain: {name}",
        "source_ranked": "Ranked: {title} (evidence {score})",
        "evidence_collected": "Evidence from {domain}: {title}",
        "citation_attached": "Citation [{marker}] → {title}",
        "provenance_linked": "Linked fact to {tool}: {fact}",
        "model_selected_tongyi": "Tongyi model selected: {model}",
        "model_forced": "Model forced by frontend settings: {model}",
        "tool_completed": "Tool {name} completed successfully.",
        "tool_invoked": "Invoked {name}",
        "tool_result_ready": "Tool {name} result ready",
        "tool_failed": "Tool {name} failed: {error}",
        "tool_approval_required": "Tool {name} requires approval before execution.",
        "execution_cancelled": "Execution cancelled at '{name}'",
        "agent_timeout": "Agent execution timed out after {timeout}s",
        "resolved_conflicts": "Resolved {count} conflicting perspective(s)",
        "follow_instructions": "Follow instructions carefully and provide concise, factual output.",
        "no_reflection_issues": "No reflection issues detected.",
        "decomposed_subgoals": "Decomposed task into {count} subgoal node(s)",
        "replanning_after": "Replanning after {agent}: {reason}",
        "graph_updated": "Graph updated: {count} recovery node(s) inserted",
        "replanning_complete": "Replanning complete — {count} nodes in execution plan",
        "spawned_child_agent": "Spawned child agent for subgoal: {subgoal}",
        "pipeline_evidence_quality": "Quality {score} — pipeline evidence present; tool retry skipped",
        "pipeline_evidence_no_tools": "Quality {score} — pipeline evidence present; per-node tool calls not required",
        "quality_retry": "Quality {score} — {reason} → {retry_type}",
        "retry_blocked": "Retry blocked: {reason}. Issues: {issues}",
        "reflection_limit": "Reflection limit reached for {agent}: {reason}",
        "injected_recovery": "Quality {score} for {agent}. Injected {retry_type} recovery step.",
        "default_task": "Complete the assigned task.",
        "default_role": "Assistant",
        "approval_required": "Approval required",
        "proceed_workflow": "Proceed with workflow execution?",
        "untitled_workflow": "Untitled Workflow",
        "execution_not_found": "Execution not found",
        "workflow_not_found": "Workflow not found",
        "workflow_deleted": "Workflow {id} not found (may have been deleted)",
        "workflow_no_nodes": "Workflow has no nodes — nothing to execute",
        "workflow_no_agents": "Workflow has no agent nodes. Add agents to the canvas first.",
        "lock_contention": "Could not start execution due to lock contention",
        "lock_contention_rerun": "Could not start rerun due to lock contention",
        "analysis_failed": "Analysis failed: {error}",
        "execution_failed": "Execution failed: {error}",
        "provider_not_allowed": "Selected provider is not allowed",
        "api_key_not_configured": "Tongyi API key is not configured",
        "model_not_allowed": "Selected model is not allowed",
        "temperature_range": "Temperature must be between 0 and 2",
        "not_waiting_approval": "Execution is not waiting for approval (current status: {status})",
        "not_waiting_approval_detail": "Execution is not waiting for approval (current status: {status}). Only 'waiting_approval' executions can be acted upon.",
        "approval_in_progress": "Another approval action is already in progress for this execution",
        "status_changed_approval": "Execution status changed before approval could be processed",
        "status_changed_rejection": "Execution status changed before rejection could be processed",
        "resume_failed": "Failed to resume execution: {error}",
        "reject_failed": "Failed to reject execution: {error}",
        "execution_rejected": "Execution rejected",
        "not_cancellable": "Execution is not cancellable (current status: {status})",
        "not_in_registry": "Execution not found in runtime registry (may have just completed)",
        "session_not_found": "Session not found",
        "benchmark_not_found": "Benchmark task {id} not found",
        "spawn_plan_not_found": "Spawn plan not found",
        "no_replay_data": "No replay data for execution",
        "approval_request_not_found": "Approval request not found",
        "backstory_researcher": "You are a meticulous research analyst. You gather facts, verify sources, and present evidence-based findings.",
        "backstory_writer": "You are a skilled content strategist. You transform raw information into clear, compelling narratives.",
        "backstory_reviewer": "You are a thorough quality assurance lead. You catch inconsistencies, validate logic, and ensure high standards.",
        "backstory_planner": "You are an organised project coordinator. You break complex goals into structured, actionable steps.",
        "backstory_coder": "You are an experienced software engineer. You write clean, documented, correct code.",
        "backstory_analyst": "You are a data-driven analyst. You extract patterns, identify trends, and produce actionable insights.",
        "backstory_default": "You are a {role}. Your goal is: {goal}",
        "api_key_missing_error": "Tongyi provider selected but API key is missing. Set it in LLM Config first.",
    },
}
