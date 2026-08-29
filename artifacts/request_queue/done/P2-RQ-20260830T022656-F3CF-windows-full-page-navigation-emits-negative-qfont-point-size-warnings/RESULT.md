result: Native Windows DPR1 Account source-popup traversal now keeps positive effective fonts and emits zero negative QFont point-size diagnostics.
changed: Added explicit positive-point QFont materialization and applied it only to the Account source popup delegate; strengthened the owning navigation regression with phase, popup/view, chart component, screenshot, and worker-quiescence assertions.
verified: Baseline reproduced 1 failed; focused native batch 8 passed; exact isolated test then passed in 3/3 fresh processes plus final 2-test pass; py_compile 3/3; diff --check and Queue Doctor OK; no provider calls or warning suppression.; independent review by fresh_gui_reviewer: Fresh independent read-only gpt-5.6-sol/high Reviewer task_f6e12a339ea4 PASS: exact hashes and three-path diff reconciled; delegate root fix has no suppression or semantic mutation; native exact 3/3, focused 8/8, final 2/2, py_compile 3/3, Doctor, layout/privacy/source-currency/quiescence/clean close all passed.
completed_at: 2026-08-30T07:03:30+09:00
review_generation: ad1de070cb97ed76ef1532c3462f2df1
reviewed_by: fresh_gui_reviewer
