"""Explicit inventory of conversation payloads stored outside SQL; not arbitrary JSON offloading.

Columns not listed here remain SQL metadata. Legacy columns stay for compatible
reads; only new/modified payloads are externalized by ConversationBodySession.
"""

BODY_FIELDS = {
    "llm_messages": ("content_text", "metadata_json"),
    "llm_runs": ("user_request",),
    "llm_tasks": ("objective",),
    "llm_context_sources": ("content_text",),
    "llm_tool_calls": ("input_json", "output_json", "error_message"),
    "llm_agent_workflow_executions": ("request_json", "error_json"),
    "llm_agent_workflow_node_executions": (
        "input_json",
        "output_json",
        "public_view_json",
        "warnings_json",
        "error_json",
    ),
    "llm_agent_workflow_events": ("public_payload_json", "public_summary"),
    "llm_model_response_events": ("public_payload_json",),
    "llm_reasoning_blocks": ("visible_text",),
    "llm_agent_handoffs": ("handoff_json",),
    "llm_run_memory_snapshots": ("state_json",),
    "llm_memory_packets": ("packet_json",),
}
