"""claude-agent-sdk 스트림 이벤트 처리 유틸리티.

데몬과 Cron 실행 양쪽에서 사용되는 StreamEvent 처리 함수와
Unix 소켓용 JSON 전송 헬퍼를 제공한다.
"""

import asyncio
import json
import logging
from typing import Any

_logger = logging.getLogger(__name__)


async def send_json(writer: asyncio.StreamWriter, data: dict[str, Any]) -> None:
    """JSON 데이터를 개행 구분으로 writer에 전송한다."""
    line = json.dumps(data, ensure_ascii=False) + "\n"
    writer.write(line.encode("utf-8"))
    await writer.drain()


async def handle_stream_event(
    message: Any,
    writer: asyncio.StreamWriter | None,
    full_text: str,
    has_stream_events: bool,
) -> tuple[str, bool]:
    """StreamEvent에서 텍스트 청크를 추출한다. writer가 지정된 경우 스트리밍 전송도 수행한다."""
    event = message.event
    if event.get("type") == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            has_stream_events = True
            chunk = delta.get("text", "")
            if chunk:
                full_text += chunk
                if writer is not None:
                    await send_json(writer, {"type": "chunk", "text": chunk})
    return full_text, has_stream_events


async def handle_assistant_message(
    message: Any,
    writer: asyncio.StreamWriter | None,
    has_stream_events: bool,
    full_text: str,
) -> tuple[str | None, str]:
    """AssistantMessage에서 모델 정보를 가져온다.

    StreamEvent 미착신 시 폴백으로 텍스트를 축적하며, writer가 지정된 경우 전송도 수행한다.
    """
    from claude_agent_sdk.types import TextBlock  # noqa: PLC0415

    current_model: str | None = message.model if hasattr(message, "model") else None
    if not has_stream_events:
        for block in message.content:
            if isinstance(block, TextBlock):
                full_text += block.text
                if writer is not None:
                    await send_json(writer, {"type": "chunk", "text": block.text})
    return current_model, full_text


async def handle_result_message(
    message: Any,
    writer: asyncio.StreamWriter | None,
    current_model: str | None,
) -> None:
    """ResultMessage를 처리한다. writer가 지정된 경우 완료 시그널을 전송한다."""
    usage = getattr(message, "usage", None) or {}
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    _logger.info(
        "query done: stop_reason=%s, model=%s, input_tokens=%d, output_tokens=%d",
        getattr(message, "stop_reason", "end_turn"),
        current_model,
        input_tokens,
        output_tokens,
    )
    if writer is not None:
        await send_json(
            writer,
            {
                "type": "done",
                "stop_reason": getattr(message, "stop_reason", "end_turn"),
                "model": current_model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_cost_usd": getattr(message, "total_cost_usd", None),
                "num_turns": getattr(message, "num_turns", 0),
            },
        )
