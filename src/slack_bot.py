"""Slack Bot. DM·채널 멘션을 claude-agent-sdk에 전달하여 답장한다."""

import asyncio
import json
import logging
import os
import re
from typing import Any

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from .config import SLACK_APP_TOKEN_ENV, SLACK_BOT_TOKEN_ENV, SOCKET_PATH
from .utils import load_config

_logger = logging.getLogger(__name__)

MAX_SLACK_LENGTH = 3000
DEFAULT_SLACK_SESSION = "slack"


async def _send_long_message(
    client: Any,
    channel: str,
    thread_ts: str | None,
    text: str,
    placeholder_ts: str | None = None,
    placeholder_channel: str | None = None,
) -> None:
    """3000자 초과 시 분할하여 전송한다.

    placeholder_ts가 지정된 경우, 첫 번째 청크는 플레이스홀더를 chat.update로
    덮어쓰고, 나머지 청크는 새 메시지로 게시한다.
    """
    for i in range(0, len(text), MAX_SLACK_LENGTH):
        chunk = text[i : i + MAX_SLACK_LENGTH]
        if i == 0 and placeholder_ts is not None:
            await client.chat_update(
                channel=placeholder_channel or channel,
                ts=placeholder_ts,
                text=chunk,
            )
        else:
            kwargs: dict[str, Any] = {"channel": channel, "text": chunk}
            if thread_ts is not None:
                kwargs["thread_ts"] = thread_ts
            await client.chat_postMessage(**kwargs)


async def _post_error_to_slack(
    client: Any,
    channel: str,
    thread_ts: str | None,
    error_text: str,
    placeholder_ts: str | None = None,
    placeholder_channel: str | None = None,
) -> None:
    """에러 메시지를 Slack에 전송한다. placeholder가 있는 경우 덮어쓴다."""
    msg = f":x: {error_text}"
    try:
        if placeholder_ts is not None:
            await client.chat_update(
                channel=placeholder_channel or channel,
                ts=placeholder_ts,
                text=msg,
            )
        else:
            kwargs: dict[str, Any] = {"channel": channel, "text": msg}
            if thread_ts is not None:
                kwargs["thread_ts"] = thread_ts
            await client.chat_postMessage(**kwargs)
    except Exception as e:  # noqa: BLE001
        _logger.error("Failed to send error message to Slack: %s", e)


class SlackBot:
    """Slack Bot. DM·채널 멘션을 claude-agent-sdk에 전달한다."""

    def __init__(self, bot_token: str, app_token: str, session_id: str, config: dict[str, Any]) -> None:
        """초기화.

        Args:
            bot_token: Slack Bot Token (xoxb-로 시작).
            app_token: Slack App Token (xapp-로 시작). Socket Mode 접속에 사용.
            session_id: 사용할 세션 별칭.
            config: config.json의 slack 섹션.
        """
        self._app = AsyncApp(token=bot_token)
        self._handler = AsyncSocketModeHandler(self._app, app_token)
        self._session_id = session_id

        dm_policy = config.get("dm_policy", "open")
        self._dm_policy: str = dm_policy if isinstance(dm_policy, str) else "open"
        allow_from = config.get("allow_from", [])
        self._allow_from: list[str] = allow_from if isinstance(allow_from, list) else []

        channel_policy = config.get("channel_policy", "open")
        self._channel_policy: str = channel_policy if isinstance(channel_policy, str) else "open"
        channels = config.get("channels", [])
        self._channels: list[str] = channels if isinstance(channels, list) else []

        ack_reaction = config.get("ack_reaction", "eyes")
        self._ack_reaction: str = ack_reaction if isinstance(ack_reaction, str) else "eyes"

        typing_message = config.get("typing_message", ":hourglass_flowing_sand: Thinking...")
        self._typing_message: str = typing_message if isinstance(typing_message, str) else ""

        # GC에 의한 태스크 파괴를 방지하기 위한 참조 유지 세트
        self._tasks: set[asyncio.Task[None]] = set()

        self._register_handlers()

    def _register_handlers(self) -> None:
        """@app.event 핸들러를 등록한다."""

        @self._app.event("message")
        async def on_dm(event: dict[str, Any], ack: Any, client: Any) -> None:
            await ack()
            if event.get("bot_id"):
                return
            if event.get("channel_type") != "im":
                return
            if self._dm_policy == "allowlist" and event.get("user") not in self._allow_from:
                return
            task = asyncio.create_task(self._handle_message(event, client, is_mention=False))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        @self._app.event("app_mention")
        async def on_mention(event: dict[str, Any], ack: Any, client: Any) -> None:
            await ack()
            if event.get("bot_id"):
                return
            if self._channel_policy == "allowlist" and event.get("channel") not in self._channels:
                return
            task = asyncio.create_task(self._handle_message(event, client, is_mention=True))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _handle_message(self, event: dict[str, Any], client: Any, is_mention: bool) -> None:
        """데몬으로의 쿼리 전송과 Slack으로의 답장을 수행한다."""
        channel: str = event.get("channel", "")
        event_ts: str = event.get("ts", "")

        if is_mention:
            raw_text: str = event.get("text", "")
            text = re.sub(r"<@[A-Z0-9]+>\s*", "", raw_text).strip()
            thread_ts: str | None = event_ts
        else:
            text = event.get("text", "")
            thread_ts = None

        # 처리 중 리액션 추가
        ack_added = False
        if self._ack_reaction and event_ts:
            try:
                await client.reactions_add(
                    name=self._ack_reaction,
                    channel=channel,
                    timestamp=event_ts,
                )
                ack_added = True
            except Exception as e:  # noqa: BLE001
                _logger.warning("Failed to add ack reaction: %s", e)

        # 타이핑 플레이스홀더 게시
        placeholder_ts: str | None = None
        placeholder_channel: str = channel
        if self._typing_message:
            try:
                ph_kwargs: dict[str, Any] = {"channel": channel, "text": self._typing_message}
                if thread_ts is not None:
                    ph_kwargs["thread_ts"] = thread_ts
                ph_resp = await client.chat_postMessage(**ph_kwargs)
                placeholder_ts = ph_resp["ts"]
                placeholder_channel = ph_resp["channel"]
            except Exception as e:  # noqa: BLE001
                _logger.warning("Failed to post typing placeholder: %s", e)

        chunks: list[str] = []
        error_msg: str | None = None

        try:
            try:
                reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
            except OSError as e:
                error_msg = f"Cannot connect to daemon: {e}"
                await _post_error_to_slack(client, channel, thread_ts, error_msg, placeholder_ts, placeholder_channel)
                return

            try:
                payload = {"type": "query", "session_id": self._session_id, "message": text}
                writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                await writer.drain()

                async for line in reader:
                    resp = json.loads(line)
                    if resp["type"] == "chunk":
                        chunks.append(resp["text"])
                    elif resp["type"] == "done":
                        break
                    elif resp["type"] == "error":
                        error_msg = resp.get("message", "unknown error")
                        break
            finally:
                writer.close()
                await writer.wait_closed()

            reply_text = f"ERROR: {error_msg}" if error_msg is not None else "".join(chunks)
            if reply_text:
                try:
                    await _send_long_message(
                        client, channel, thread_ts, reply_text, placeholder_ts, placeholder_channel
                    )
                except Exception as e:  # noqa: BLE001
                    _logger.error("Slack send error: %s", e)
                    await _post_error_to_slack(client, channel, thread_ts, str(e), placeholder_ts, placeholder_channel)
            elif placeholder_ts is not None:
                # 응답이 비어 있는 경우 플레이스홀더를 삭제
                try:
                    await client.chat_delete(channel=placeholder_channel, ts=placeholder_ts)
                except Exception as e:  # noqa: BLE001
                    _logger.warning("Failed to delete empty placeholder: %s", e)
        finally:
            # 처리 중 리액션 제거
            if ack_added:
                try:
                    await client.reactions_remove(
                        name=self._ack_reaction,
                        channel=channel,
                        timestamp=event_ts,
                    )
                except Exception as e:  # noqa: BLE001
                    _logger.warning("Failed to remove ack reaction: %s", e)

    async def start(self) -> None:
        """Bot을 시작한다. 데몬의 asyncio.gather에서 호출한다."""
        try:
            auth = await self._app.client.auth_test()
            _logger.info("Slack bot ready (logged in as %s, team=%s)", auth["user"], auth["team"])
        except Exception as e:  # noqa: BLE001
            _logger.warning("Slack bot auth_test failed: %s", e)
        await self._handler.start_async()

    async def stop(self) -> None:
        """Bot을 정지한다. 데몬 셧다운 시 호출한다."""
        for task in list(self._tasks):
            task.cancel()
        if hasattr(self._handler, "close_async"):
            await self._handler.close_async()
        elif hasattr(self._handler, "close"):
            await self._handler.close()


def _load_slack_config() -> dict[str, Any]:
    """config.json에서 slack 섹션을 읽어들인다. 실패 시 빈 dict를 반환한다."""
    section = load_config().get("slack", {})
    return section if isinstance(section, dict) else {}


def create_slack_bot() -> "SlackBot | None":
    """config.json 또는 환경 변수에서 설정을 읽어들여 SlackBot을 반환한다.

    bot_token 또는 app_token이 미설정인 경우 None을 반환한다 (Bot 없이 데몬 기동 계속).
    """
    cfg = _load_slack_config()

    # Bot Token: config.json → 환경 변수 순서로 폴백
    bot_token: str | None = cfg.get("bot_token") or os.environ.get(SLACK_BOT_TOKEN_ENV)  # noqa: S105
    if not bot_token:
        _logger.warning("Slack bot disabled: bot_token not set")
        return None

    # App Token: config.json → 환경 변수 순서로 폴백
    app_token: str | None = cfg.get("app_token") or os.environ.get(SLACK_APP_TOKEN_ENV)  # noqa: S105
    if not app_token:
        _logger.warning("Slack bot disabled: app_token not set")
        return None

    session_id: str = cfg.get("session_id", DEFAULT_SLACK_SESSION)
    _logger.info("Slack bot starting (session=%s)", session_id)
    return SlackBot(bot_token=bot_token, app_token=app_token, session_id=session_id, config=cfg)
