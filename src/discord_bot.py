"""Discord Bot. 지정 채널의 메시지를 claude-agent-sdk에 전달하여 답장한다."""

import asyncio
import json
import logging
import os
from typing import Any

import discord

from .config import DISCORD_BOT_TOKEN_ENV, SOCKET_PATH
from .utils import load_config

_logger = logging.getLogger(__name__)

MAX_DISCORD_LENGTH = 2000
DEFAULT_DISCORD_SESSION = "discord"


async def _send_long_message(channel: discord.abc.Messageable, text: str) -> None:
    """Discord 메시지 글자 수 상한(2000자)을 초과할 경우 분할 전송한다."""
    for i in range(0, len(text), MAX_DISCORD_LENGTH):
        await channel.send(text[i : i + MAX_DISCORD_LENGTH])


class _DiscordClient(discord.Client):
    """discord.Client의 서브클래스. on_ready / on_message를 오버라이드하여 처리한다."""

    def __init__(self, channel_id: int, session_id: str, ack_reaction: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._channel_id = channel_id
        self._session_id = session_id
        self._ack_reaction = ack_reaction
        # GC에 의한 태스크 파괴를 방지하기 위한 참조 유지 세트
        self._tasks: set[asyncio.Task[None]] = set()

    async def on_ready(self) -> None:
        """접속 완료 이벤트."""
        channel = self.get_channel(self._channel_id)
        if channel is None:
            _logger.warning("Discord bot: channel %d not found", self._channel_id)
        _logger.info("Discord bot ready (logged in as %s)", self.user)

    async def on_message(self, message: discord.Message) -> None:
        """메시지 수신 이벤트. 소켓 통신을 독립 태스크에 위임한다."""
        if message.channel.id != self._channel_id:
            return
        if message.author == self.user:
            return
        # discord.py가 이 태스크를 취소해도 통신이 끊기지 않도록 독립 태스크로 처리한다
        # 태스크 참조를 _tasks에 유지하여 GC에 의한 파괴를 방지한다
        task = asyncio.create_task(self._handle_message(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle_message(self, message: discord.Message) -> None:
        """데몬으로의 쿼리 전송과 Discord로의 답장을 수행한다."""
        # 처리 중 리액션 추가
        ack_added = False
        if self._ack_reaction:
            try:
                await message.add_reaction(self._ack_reaction)
                ack_added = True
            except discord.HTTPException as e:
                _logger.warning("Failed to add ack reaction: %s", e)

        try:
            try:
                reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
            except OSError:
                await _send_long_message(
                    message.channel,  # type: ignore[arg-type]
                    "ERROR: Cannot connect to daemon",
                )
                return

            chunks: list[str] = []
            error_msg: str | None = None
            async with message.channel.typing():  # type: ignore[union-attr]
                try:
                    payload = {"type": "query", "session_id": self._session_id, "message": message.content}
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

            if error_msg is not None:
                await _send_long_message(
                    message.channel,  # type: ignore[arg-type]
                    f"ERROR: {error_msg}",
                )
                return

            full_response = "".join(chunks)
            if full_response:
                try:
                    await _send_long_message(
                        message.channel,  # type: ignore[arg-type]
                        full_response,
                    )
                except discord.HTTPException as e:
                    _logger.error("Discord send error: %s", e)
        finally:
            # 처리 중 리액션 제거
            if ack_added:
                try:
                    await message.remove_reaction(self._ack_reaction, self.user)  # type: ignore[arg-type]
                except discord.HTTPException as e:
                    _logger.warning("Failed to remove ack reaction: %s", e)


class DiscordBot:
    """Discord Bot. 지정 채널의 메시지를 claude-agent-sdk에 전달한다."""

    def __init__(self, token: str, channel_id: int, session_id: str, config: dict[str, Any]) -> None:
        """초기화.

        Args:
            token: Discord Bot Token.
            channel_id: 대상 채널 ID.
            session_id: 사용할 세션 별칭.
            config: config.json의 discord 섹션.
        """
        intents = discord.Intents.default()
        intents.message_content = True
        ack_reaction = config.get("ack_reaction", "👀")
        ack_reaction = ack_reaction if isinstance(ack_reaction, str) else "👀"
        self._client = _DiscordClient(
            channel_id=channel_id, session_id=session_id, ack_reaction=ack_reaction, intents=intents
        )
        self._token = token

    async def start(self) -> None:
        """Bot을 시작한다. 데몬의 asyncio.gather에서 호출한다."""
        async with self._client:
            await self._client.start(self._token)

    async def stop(self) -> None:
        """Bot을 정지한다. 데몬 셧다운 시 호출한다."""
        for task in list(self._client._tasks):
            task.cancel()
        if not self._client.is_closed():
            await self._client.close()


def _load_discord_config() -> dict[str, Any]:
    """config.json에서 discord 섹션을 읽어들인다. 실패 시 빈 dict를 반환한다."""
    section = load_config().get("discord", {})
    return section if isinstance(section, dict) else {}


def create_discord_bot() -> "DiscordBot | None":
    """config.json 또는 환경 변수에서 설정을 읽어들여 DiscordBot을 반환한다.

    Token 또는 channel_id가 미설정인 경우 None을 반환한다 (Bot 없이 데몬 기동 계속).
    """
    cfg = _load_discord_config()

    # Token: config.json → 환경 변수 순서로 폴백
    token: str | None = cfg.get("bot_token") or os.environ.get(DISCORD_BOT_TOKEN_ENV)  # noqa: S105
    if not token:
        _logger.warning(
            "Discord bot disabled: %s not set and discord.bot_token not in config",
            DISCORD_BOT_TOKEN_ENV,
        )
        return None

    # 채널 ID: config.json에서만 가져옴 (하드코딩 없음)
    raw_channel_id = cfg.get("channel_id")
    if raw_channel_id is None:
        _logger.warning("Discord bot disabled: discord.channel_id not set in config")
        return None
    try:
        channel_id = int(raw_channel_id)
    except (ValueError, TypeError):
        _logger.warning("Discord bot disabled: discord.channel_id is invalid: %r", raw_channel_id)
        return None

    session_id: str = cfg.get("session_id", DEFAULT_DISCORD_SESSION)
    _logger.info("Discord bot starting (channel_id=%d, session=%s)", channel_id, session_id)
    return DiscordBot(token=token, channel_id=channel_id, session_id=session_id, config=cfg)
