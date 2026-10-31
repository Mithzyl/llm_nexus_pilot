"""Fixed-model Python workflow for the isolated browser fixture; no provider HTTP requests.

Enable with serve_identity_memory_fixture.py --python-continuation --worker.
Uses the prebuilt local runtime image, isolated MySQL and real MinIO. PYTHON_STOP
in the user message selects a long second command for explicit cancellation.
"""

import json
from nexuspilot_models.contracts import ModelResponse, ToolCall


async def generate_python_step(request):
    """Read an explicitly selected project file when requested, then run two real Python calls."""
    tools = [message for message in request.messages if message.role.value == "tool"]
    executions = [json.loads(message.content) for message in tools if message.tool_name == "run_command"]
    project = any("PYTHON_PROJECT" in (message.content or "") for message in request.messages if message.role.value == "user")
    listed = [json.loads(message.content) for message in tools if message.tool_name == "list_run_files"]
    if project and not listed:
        call = ToolCall(id="project-files", name="list_run_files", arguments={})
    elif not any(message.tool_name == "describe_tools" for message in tools):
        call = ToolCall(id="python-load", name="describe_tools", arguments={"name":"run_command", "version":"1.0.0"})
    elif not executions:
        source = "from pathlib import Path\nPath('intermediate.txt').write_text('21')\nprint('first saved')"
        file_ids = []
        if project:
            files = listed[-1]["result"]["files"]
            if len(files) != 1:
                raise RuntimeError("Project fixture must expose exactly the selected file")
            file_ids = [files[0]["file_id"]]
            source = ("from pathlib import Path\nvalue=Path('/input/" + file_ids[0] + "').read_text()\n"
                      "Path('intermediate.txt').write_text(value)\nprint('selected project input saved')")
        call = ToolCall(id="python-first", name="run_command", arguments={
            "stdin":source, "file_ids":file_ids, "output_files":[]})
    elif len(executions) == 1:
        stop = any("PYTHON_STOP" in (message.content or "") for message in request.messages if message.role.value == "user")
        code = ("import time\ntime.sleep(120)" if stop else
                "from pathlib import Path\nvalue=int(Path('intermediate.txt').read_text())*2\n"
                "Path('answer.txt').write_text(str(value))\nprint(value)")
        call = ToolCall(id="python-second", name="run_command", arguments={"stdin":code, "output_files":["answer.txt"]})
    else:
        if any(item["status"] != "completed" for item in executions):
            raise RuntimeError("Synthetic Python workflow requires successful actual tools")
        return ModelResponse(text="PYTHON_CONTINUATION " + json.dumps(executions[-1]["result"]),
            finish_reason="stop", latency_ms=1, raw_response={"fixture":True})
    return ModelResponse(tool_calls=[call], finish_reason="tool_calls", latency_ms=1,
                         raw_response={"fixture":True})
