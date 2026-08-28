reason: Fresh Orca Worker dispatch ctx_3360ef20de13 failed at dispatch_input with agent_prompt_stalled before implementation began; cleanup also returned release_unknown/tab_not_found.
required_action: Inspect Orca dispatch ctx_3360ef20de13 and the reused terminal mapping; do not extend this failed Run with another retry or recovery chain.
resume_condition: Start from a new Run only after Orca can create and prompt a fresh task-scoped Worker terminal without dispatch_input failure.
