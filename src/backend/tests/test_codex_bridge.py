from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.services import codex_bridge as module


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Codex bridge tests")


def _write_fake_bridge(tmp_path: Path, source: str) -> Path:
    bridge_path = tmp_path / "fake-codex-bridge.mjs"
    bridge_path.write_text(source, encoding="utf-8")
    return bridge_path


@pytest.mark.asyncio
async def test_codex_bridge_client_collects_streamed_turn(tmp_path: Path) -> None:
    bridge_path = _write_fake_bridge(
        tmp_path,
        """
import process from "node:process";

let payload = "";
process.stdin.setEncoding("utf8");
for await (const chunk of process.stdin) {
  payload += chunk;
}
const request = JSON.parse(payload);
const threadId = request.thread_id || "thread-1";
const write = (record) => process.stdout.write(`${JSON.stringify(record)}\\n`);

write({ type: "event", event: { type: "thread.started", thread_id: threadId } });
write({
  type: "event",
  event: {
    type: "item.completed",
    item: { id: "item-1", type: "agent_message", text: `done:${request.input[0].text}` },
  },
});
write({
  type: "event",
  event: {
    type: "turn.completed",
    usage: { input_tokens: 1, cached_input_tokens: 0, output_tokens: 2, reasoning_output_tokens: 0 },
  },
});
write({
  type: "complete",
  thread_id: threadId,
  final_response: `done:${request.input[0].text}`,
  usage: { input_tokens: 1, cached_input_tokens: 0, output_tokens: 2, reasoning_output_tokens: 0 },
});
""",
    )
    captured_events: list[dict] = []
    client = module.CodexBridgeClient(
        bridge_script=bridge_path,
        api_key="test-key",
        extra_env={"PATH": "test-path", "HTTPS_PROXY": "http://proxy.internal:8080"},
    )

    result = await client.run_turn(
        input=[
            {"type": "text", "text": "render status"},
            {"type": "local_image", "path": "C:/data/uploads/session/image.png"},
        ],
        thread_id="thread-existing",
        working_directory="C:/workspace",
        on_event=captured_events.append,
    )

    assert result.thread_id == "thread-existing"
    assert result.final_response == "done:render status"
    assert result.usage == {
        "input_tokens": 1,
        "cached_input_tokens": 0,
        "output_tokens": 2,
        "reasoning_output_tokens": 0,
    }
    assert [event["type"] for event in captured_events] == [
        "thread.started",
        "item.completed",
        "turn.completed",
    ]


@pytest.mark.asyncio
async def test_codex_bridge_client_raises_bridge_error_record(tmp_path: Path) -> None:
    bridge_path = _write_fake_bridge(
        tmp_path,
        """
import process from "node:process";
process.stdout.write(JSON.stringify({ type: "error", message: "bridge failed" }) + "\\n");
process.exitCode = 1;
""",
    )
    client = module.CodexBridgeClient(bridge_script=bridge_path, api_key="test-key")

    with pytest.raises(module.CodexBridgeError) as exc_info:
        await client.run_turn(input="hello")

    assert str(exc_info.value) == "bridge failed"
    assert exc_info.value.exit_code == 1
    assert exc_info.value.record["message"] == "bridge failed"


def test_build_codex_input_uses_local_images_for_persisted_attachments() -> None:
    codex_input = module.build_codex_input(
        "describe this",
        [
            {"type": "image", "file_path": "C:/data/uploads/session/reference.png"},
            {"type": "image", "data_url": "data:image/png;base64,QUJD"},
        ],
    )

    assert codex_input == [
        {"type": "text", "text": "describe this"},
        {"type": "local_image", "path": "C:/data/uploads/session/reference.png"},
    ]


def test_codex_bridge_request_disables_responses_websocket_for_custom_base_url() -> None:
    client = module.CodexBridgeClient(
        api_key="test-key",
        base_url="http://codex.internal.test/v1",
        disable_responses_websocket=True,
    )

    request = client.build_request(input="hello")

    assert request["base_url"] == "http://codex.internal.test/v1"
    assert request["disable_responses_websocket"] is True
