# One final file per chat response

User approved implementation in the current conversation, with interactive acceptance performed by the user. Work on `fix/chat-final-artifact`; preserve the real `.env` and database. Do not spend time reproducing the intermittent report with a live model.

Contract: stream response text as received, then show one verified final output card after successful completion. Keep intermediate immutable versions in storage and the version picker. Use the newest published output in the run as the final file. Handle historical multi-output messages consistently. Preserve multiple outputs in the Automation results view.

- [x] Add focused deterministic stream tests for early artifact events, multiple outputs, replay, snapshot and cancellation; no live API or database.
- [x] Select one final output for new message bindings, historical message reads and chat run timelines; include verified final refs in the completion event.
- [x] Buffer artifact events per chat timeline until completion; render only the final card, including restored/polled completed messages. Keep text deltas live and preview closed unless already open.
- [x] Run targeted checks, inspect the diff and record results. User performs browser acceptance. No merge or push in this task.

Review focus: replay must not restore an older output; reconnect/reset must not expose intermediate cards; active timeline polling must not expose cards before completion; failed/cancelled runs must not advertise an intermediate result as final; Automation results must retain their existing multi-file behavior.

## Verification

- `node tests/chat_final_artifact_stream.cjs`: 10 passed. Before the change, the initial stream cases exposed early/multiple cards; the reset fixture was corrected to supply the existing timeline title element.
- Targeted pytest (`test_chat_final_artifact`, `test_agent_timeline`, `test_artifact_tools`, `test_background_jobs`): 19 passed, 7 Docker/database-gated tests skipped. Tests disabled dotenv loading and used fake credentials; no user database was contacted. Sandbox temp-directory failures were resolved by running with an explicit project-local temp directory and appropriate filesystem permission.
- Ruff check and format check, Node syntax checks for both changed JavaScript files, and `git diff --check` passed.
- Independent code review: no Critical or Important findings.
- Interactive/browser acceptance, live-model reproduction and the full suite were not run; the user chose to perform hands-on testing. No schema migration or configuration change is required.
