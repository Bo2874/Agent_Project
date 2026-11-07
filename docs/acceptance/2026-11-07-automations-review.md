# Plan04 — independent whole-branch review

Reviewed range `df49358..a966dff` using a fresh `gpt-6-astra` reviewer, read-only.
The review briefly hit a tool usage limit; the same reviewer subsequently returned
its complete report without a second review or additional reviewer. No live providers,
credentials, user DB, or broad tests were used by the reviewer.

Original verdict: **not ready to merge**. No Critical established; seven Important findings.
The resolution evidence belongs to the implementation pass after this report, not a re-review.

## Important findings

1. **Terminal lease recovery left automation active.** Queue terminal projection handled
   `agent.run` only. Killing a worker after Stop or exhausting attempts stranded
   `AutomationRun` in cancelling/running. Real-DB probes confirmed both.
2. **Approval projection raced persistence.** The approval callback could synchronize
   before the recorder persisted waiting state, hiding the durable approval indefinitely.
   Static review; subsequently reproduced by delaying the approval recorder.
3. **Sequential approval notices collided.** A run/status-only event key discarded a new
   approval after the first notice was read. Real-DB probe confirmed one notice instead of two.
4. **Old active runs disappeared behind newest-100 limits.** After 100 newer skipped ticks,
   ordinary history/inbox discovery lost controls for the blocking active run. Direct saved
   links still worked. Real-DB probe confirmed exclusion.
5. **Paid tool replay double-counted usage.** Synthetic model-start replay populated the
   recorder step map, bypassing ToolCall restoration. Replayed cached search data produced
   another ToolCall and 340 reported tokens instead of 220. Real-DB probe confirmed this.
6. **Generic chat writes could mutate execution conversations.** Posting created unrelated
   messages/runs, with chat-default execution or a doomed durable run; result display could
   concatenate unrelated assistant replies. Static endpoint/UI trace; subsequently reproduced
   for both ReAct settings, and for all three working-file selection routes.
7. **Scheduled instructions were re-extracted into personal memory.** Production default
   extraction treated each synthetic scheduled message as a new personal assertion, potentially
   superseding newer facts or renewing retention. Static default/conflict-resolution trace;
   subsequently reproduced at the extraction hook without a paid model.

The reviewer established no separate Minor. The implementer grades general terminal-history
and inbox pagination as a deferred Minor: active control visibility must be fixed now, while
the documented newest-100 historical browsing limit remains in this version.

## Declined to judge, and final rulings

1. Live Google OAuth/refresh/Sheets/Docs and paid-model quality remain the user's deferred
   acceptance gate. Cost: cloud behavior and model quality remain unverified until that gate.
2. Large distributed-load capacity is not claimed. Cost: production capacity needs separate testing.
3. Native append search-range semantics stand. Cost: physical appended rows are not confined
   to that range under concurrent external edits.
4. Same-run new Docs may be composed under receipt/generation constraints. Cost: that new
   output can be edited automatically; pre-existing Docs require separate grants.
5. Pending unattended approval keeps a worker and active slot. Cost: reduced worker capacity
   until the owner decides or stops the run.
6. Inbox polling remains about ten seconds. Cost: badges can lag by that interval.
7. Headless orchestration continues reusing the chat route module. Cost: maintenance coupling
   remains, while the behavioral defects above must be fixed.
8. Dedicated execution-history deletion/retention remains deferred; generic deletion is blocked.
   Cost: audit data continues accumulating until a retention policy is implemented.
9. Automation deployment requires ReAct as documented. Cost: turning ReAct off does not provide
   a supported fallback automation executor.
10. Missing `data.usage` in the isolated search fixture is not a separate production defect:
    real search supplies it. The fixture is corrected and exact journal/timeline totals are checked.
    Cost: future paid tools must preserve the established usage contract or add canonical accounting.
11. Unrelated Plan01–03 issues, OCR/images, webhooks/condition triggers and workflow builders
    remain outside Plan04. Cost: those capabilities require their own later work.

## Resolution status

The single implementation fix pass is complete. All seven Important findings have covering
regressions observed RED before production changes, then GREEN. Focused verification has
**17 passed** (138.19s); final standard whole-suite verification has **1,304 passed / 59
conditional skips** (373.30s). No Important/Critical finding from this review remains open.
This conclusion is test-based implementation verification, not a second independent review.

| Finding | Regression and implemented behavior |
| --- | --- |
| Terminal lease recovery | `test_terminal_lease_recovery_releases_automation_state`, `test_terminal_projection_is_retried_after_queue_commit`, `test_terminal_lease_before_agent_admission_releases_slot`: worker/scheduler repair terminal job projections; uncertain operations retain reconciliation. |
| Approval persistence race | `test_waiting_approval_projection_does_not_depend_on_callback_order`: synchronize after both durable approval and recorder state exist, whichever arrives last. |
| Repeated approval notice | `test_second_approval_has_unread_notice`: approval identity separates events; replay remains idempotent. |
| Hidden active run | `test_old_active_run_remains_listed`: active run sorts before newest terminal history. |
| Duplicate paid usage | `test_replayed_paid_tool_usage_is_not_counted_twice`: restore persisted tool identity even after synthetic model-start replay. |
| Execution conversation writes | `test_execution_conversation_rejects_new_chat_message`, `test_execution_conversation_rejects_work_selection_changes`: reject new chat messages and all three selection mutations before side effects. |
| Scheduled personal-memory extraction | `test_synthetic_automation_instruction_does_not_extract_personal_memory`: synthetic scheduled messages do not become new personal assertions; retrieval remains available. |

Expanded process testing initially returned **37 passed / 1 failed** (415.69s): a replacement
worker lost the fixture's artificial two-second lease. A diagnostic process-only rerun
returned two `missed` tick assertions during cold setup and one 60-second child timeout
(334.42s). The fixture now uses a normal 30-second lease, explicitly expires only the
confirmed-dead child's job, publishes the tick after checkpoint setup, and permits 180 seconds
for child startup. Production scheduler grace and worker lease defaults are unchanged.
These failed runs are not counted as successful recovery evidence.

The extended all-Docker regression then **finished naturally: 1,357 passed / 5 failed /
1 Windows symlink skip**, 2,535.27s. No process was stopped: an identity check refused the
planned stop because pytest had already exited. A read-only stack snapshot during the long
run showed the event loop waiting, without PostgreSQL lock contention; it did not establish
a deadlock. Temporary-directory cleanup also reported WinError 145 in the failed scratch tree.

The five failures were: a 0.1s test deadline expiring before tool admission (correctly returning
`agent_budget_skipped`), a transient held Windows lease after the fixture child died, two workbook
child containers exiting 137, an overlap test racing the legitimate scheduler `SKIP LOCKED`,
and a browser DOCX tool failure after about 59 seconds. The first, second and fourth are now
tested with explicit readiness: graph setup time before an admitted never-ending tool,
released child lease before reclamation, and committed approval notice before a single tick.
The complete scratch/timing group is **9 passed** (30.07s); product timeout, sandbox,
permission, lease and reaper policies are unchanged. No production Artifact behavior was changed.

The unchanged workbook benchmark passed in an isolated Docker rerun: 10,000 rows × 12 columns,
two sheets, a chart, cached sum 50,005,000, and two distinct concurrent revisions retaining the
parent. Benchmark duration 167.04s, sampled maximum 354.5 MiB. This is local acceptance evidence,
not a distributed-capacity claim. The isolated Docker group, including the real Document
browser/worker flow, finished **2 passed** (350.84s), without changing production resource
limits or Artifact behavior. The final standard whole suite includes the corrected process
fixture and all 17 review regressions: **1,304 passed / 59 conditional skips** (373.30s),
exit 0. The skips include optional Docker gates (covered by the extended run and focused
reruns) and Windows symlink permission. The `cache_dir` warning results from disabling
pytest's cache provider. Ruff, JavaScript syntax, Compose profile validation and diff checks
also passed. Live Google and paid models remain **NOT RUN** by the user's decision.

The three real PostgreSQL/process recovery variants passed with exact journal/timeline
usage agreement: 50,000 tokens for either successful restart, 40,000 known tokens plus
unknown-outcome fencing for a lost write response. Each provider state retained exactly one
append; no repeated paid model/tool call or hidden zero-token billing was accepted.

Deferred Minor: terminal-history and Inbox pagination beyond the disclosed newest-100 limit.
The blocking active run is pinned first, so its controls remain discoverable.
