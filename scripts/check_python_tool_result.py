import asyncio
import sys
from uuid import uuid4

from agent_core.tools import ToolContext, ToolExecutor
from backend.dependencies_tools import get_tool_registry

TOOL_CODE = """
import json
import os
import sys
from pathlib import Path

artifact_dir = Path(os.environ['AGENT_ARTIFACT_DIR'])
(artifact_dir / 'answer.json').write_text(
    json.dumps({'answer': 6}), encoding='utf-8'
)
print('6')
print('checked warning', file=sys.stderr)
""".strip()


async def run_check() -> int:
    registry = get_tool_registry()
    executor = ToolExecutor()
    context = ToolContext(
        user_id=uuid4(),
        conversation_id=uuid4(),
        user_role="user",
        run_id=uuid4(),
    )

    denied = await executor.execute(
        registry,
        context,
        "run_python_code",
        {"code": TOOL_CODE},
    )
    approved = await executor.execute(
        registry,
        context,
        "run_python_code",
        {"code": TOOL_CODE},
        approval_granted=True,
    )
    artifacts = approved.data.get("artifacts", [])
    checks = {
        "approval_required": denied.error_code == "tool_approval_required",
        "tool_success": not approved.is_error,
        "stdout": approved.data.get("stdout") == "6\n",
        "stderr": approved.data.get("stderr") == "checked warning\n",
        "untrusted": approved.data.get("output_trust") == "untrusted",
        "artifact": len(artifacts) == 1 and artifacts[0].get("name") == "answer.json",
    }
    for name, passed in checks.items():
        print(f"{name}: {'PASSED' if passed else 'FAILED'}")
    all_passed = all(checks.values())
    if not all_passed:
        print(f"tool_error_code: {approved.error_code}")
        print(f"tool_content: {approved.content}")
        print(f"tool_data: {dict(approved.data)}")
    print("PYTHON_TOOL_RESULT_PASSED" if all_passed else "PYTHON_TOOL_RESULT_FAILED")
    return 0 if all_passed else 1


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(run_check()))


if __name__ == "__main__":
    main()
