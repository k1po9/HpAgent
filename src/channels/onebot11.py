"""Authenticated reverse OneBot v11 WebSocket surface for SnowLuma 2.2.0.

The reader never awaits business commands. Connections, pending RPCs and inbound
queues are bounded; echoes belong to one connection and one sending attempt.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from time import monotonic
from uuid import uuid4

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from channels.base import BaseChannel
from common.channel_result import ChannelSendResult
from common.onebot import OneBot11Config
from common.types import ChannelType, UnifiedMessage

logger = logging.getLogger(__name__)
# Protocol DEBUG logging includes frames and Authorization headers.
transport_logger = logging.getLogger('HpAgent.OneBotTransport')
transport_logger.setLevel(logging.WARNING)
_CQ = re.compile(r'\[CQ:([^,\]]+)((?:,[^\]]*)?)\]')


def _cq_segments(text):
    """Compatibility for inbound CQ strings only; outbound text stays literal."""
    def decode(value):
        return value.replace('&#44;', ',').replace('&#91;', '[').replace('&#93;', ']').replace('&amp;', '&')

    segments, end = [], 0
    for match in _CQ.finditer(text):
        if match.start() > end:
            segments.append({'type': 'text', 'data': {'text': decode(text[end:match.start()])}})
        data = dict(item.split('=', 1) for item in match[2].lstrip(',').split(',') if '=' in item)
        segments.append({'type': match[1], 'data': {k: decode(v) for k, v in data.items()}})
        end = match.end()
    if end < len(text):
        segments.append({'type': 'text', 'data': {'text': decode(text[end:])}})
    return segments


@dataclass(eq=False)
class _Connection:
    ws: object
    bot: str
    epoch: int
    pending: dict = field(default_factory=dict)
    verified: bool = False
    online: bool = False
    good: bool = False
    status_at: float = 0
    version: str = ''


class OneBot11Channel(BaseChannel):
    def __init__(self, config: OneBot11Config | None = None):
        super().__init__(ChannelType.ONEBOT11)
        self.config = config or OneBot11Config()
        self.bot_name = 'bot'
        self._server = None
        self._connection = None
        self._epoch = 0
        self._connections: set[_Connection] = set()
        self._queue = asyncio.Queue(maxsize=self.config.ingress_queue_size)
        self._consumer = None
        self._send_lock = asyncio.Lock()
        self._last_send = 0
        self._token = ''
        self.counters = Counter()
        self._last_event = None
        self._last_ack = None

    @property
    def listening(self):
        return bool(self._monitoring and self._server and self._server.is_serving())

    @property
    def ready(self):
        c = self._connection
        return bool(self.listening and self._consumer and not self._consumer.done()
                    and c and c.verified and c.online and c.good
                    and monotonic() - c.status_at < self.config.status_stale_seconds)

    def diagnostics(self):
        c = self._connection
        return {'listening': self.listening, 'ready': self.ready,
                'connected': bool(c), 'online': bool(c and c.online),
                'good': bool(c and c.good), 'version': c.version if c else None,
                'epoch': c.epoch if c else None, 'queue_depth': self._queue.qsize(),
                'last_event': self._last_event, 'last_ack': self._last_ack,
                'counters': dict(self.counters)}

    async def normalize_message(self, raw_message):
        data = json.loads(raw_message) if isinstance(raw_message, (str, bytes)) else raw_message
        if not isinstance(data, dict):
            raise ValueError('OneBot event must be an object')
        if data.get('post_type') != 'message':
            return None
        scope, bot = data.get('message_type'), str(data.get('self_id') or '')
        for field_name in ('self_id', 'user_id', 'group_id'):
            value = data.get(field_name)
            if value is not None and (not isinstance(value, (int, str)) or isinstance(value, bool)):
                raise ValueError('invalid OneBot identity field')
        sender = data.get('sender') or {}
        if not isinstance(sender, dict):
            raise ValueError('invalid sender')
        if sender.get('user_id') is not None and (not isinstance(sender['user_id'], (int, str)) or isinstance(sender['user_id'], bool)):
            raise ValueError('invalid OneBot sender identity')
        user = str(data.get('user_id') or sender.get('user_id') or '')
        if data.get('user_id') and sender.get('user_id') and str(data['user_id']) != str(sender['user_id']):
            raise ValueError('conflicting sender identity')
        message_id = data.get('message_id')
        if scope not in {'private', 'group'} or not bot or not user or message_id in (None, ''):
            raise ValueError('missing OneBot message identity')
        if not isinstance(message_id, (int, str)) or isinstance(message_id, bool):
            raise ValueError('invalid OneBot message ID')
        if scope == 'group' and not data.get('group_id'):
            raise ValueError('missing OneBot group identity')
        metadata = {'post_type': 'message', 'detail_type': scope,
                    'sub_type': data.get('sub_type', ''), 'self_id': bot,
                    'message_id': message_id, 'sender_name': str(sender.get('card') or sender.get('nickname') or ''),
                    'thread_id': str(data.get('thread_id') or ''), 'is_at_bot': False}
        if data.get('time') is not None:
            metadata['iso_timestamp'] = datetime.fromtimestamp(float(data['time']), UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        if data.get('group_id') is not None:
            metadata['group_id'] = data['group_id']
        if scope == 'private' and data.get('sub_type') == 'group' and not data.get('group_id'):
            raise ValueError('temporary private message requires source group')
        segments = data.get('message', data.get('raw_message', ''))
        if isinstance(segments, str):
            segments = _cq_segments(segments)
        if not isinstance(segments, list):
            raise ValueError('invalid OneBot segments')
        texts, images, unsupported, has_at = [], [], [], False
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get('data'), dict):
                raise ValueError('invalid OneBot segment')
            kind, value = segment.get('type'), segment['data']
            if kind == 'text':
                texts.append(str(value.get('text', '')))
            elif kind == 'at':
                has_at = True
                metadata['is_at_bot'] |= str(value.get('qq')) == bot
            elif kind == 'image':
                url = value.get('url') or value.get('file') or ''
                if isinstance(url, str) and url.startswith(('https://', 'http://')):
                    images.append(url)
                texts.append('[图片]')
            elif kind not in {'reply', 'face'}:
                unsupported.append(str(kind))
                texts.append('[暂不支持的消息段]')
        if not has_at and self.bot_name != 'bot':
            metadata['is_at_bot'] = bool(re.search(rf'@\s*{re.escape(self.bot_name)}(?!\w)', ''.join(texts), re.I))
        if images:
            metadata.update(image_urls=images, _images=images, _image_count=len(images))
        if unsupported:
            metadata['unsupported_segments'] = unsupported
            self.counters['unsupported_segments'] += len(unsupported)
        return UnifiedMessage(message_id=str(message_id), sender_id=user, content=''.join(texts).strip(),
                              channel_type=ChannelType.ONEBOT11, metadata=metadata)

    def _handshake(self, connection, request):
        try:
            valid = (request.path == self.config.path
                     and hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + self._token)
                     and request.headers.get('X-Self-ID') == self.config.expected_self_id
                     and request.headers.get('X-Client-Role') == 'Universal'
                     and len(self._connections) < 2)
        except Exception:
            valid = False
        if not valid:
            self.counters['handshake_rejected'] += 1
            return connection.respond(HTTPStatus.UNAUTHORIZED, 'OneBot connection rejected\n')
        return None

    async def _rpc(self, c, action, params=None, attempt='probe'):
        if len(c.pending) >= 32:
            raise RuntimeError('OneBot pending bound')
        echo = f'{attempt}:{c.epoch}:{uuid4().hex}'
        future = asyncio.get_running_loop().create_future()
        c.pending[echo] = future
        try:
            await c.ws.send(json.dumps({'action': action, 'params': params or {}, 'echo': echo}))
            response = await asyncio.wait_for(future, self.config.ack_timeout_seconds)
            return response, echo
        finally:
            c.pending.pop(echo, None)

    @staticmethod
    def _ok(response):
        return response.get('status') == 'ok' and type(response.get('retcode')) is int and response['retcode'] == 0

    async def _probe(self, c):
        login, _ = await self._rpc(c, 'get_login_info')
        version, _ = await self._rpc(c, 'get_version_info')
        if not self._ok(login) or str(login.get('data', {}).get('user_id')) != c.bot:
            raise ValueError('OneBot login identity mismatch')
        v = version.get('data') or {}
        if not self._ok(version) or v.get('app_name') != 'SnowLuma' or v.get('app_version') != '2.2.0-node' or v.get('protocol_version') != 'v11':
            raise ValueError('OneBot implementation/version mismatch')
        c.version = v['app_version']
        await self._status(c)

    async def _status(self, c):
        response, _ = await self._rpc(c, 'get_status')
        status = response.get('data') or {}
        c.online = self._ok(response) and status.get('online') is True
        c.good = self._ok(response) and status.get('good') is True
        c.status_at = monotonic()

    async def _read(self, c):
        try:
            async for raw in c.ws:
                try:
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise ValueError('frame object required')
                    if 'self_id' in data and str(data['self_id']) != c.bot:
                        self.counters['identity_rejected'] += 1
                        await c.ws.close(code=1008, reason='identity mismatch')
                        break
                    if 'echo' in data:
                        future = c.pending.get(data['echo']) if isinstance(data['echo'], str) else None
                        if future is not None and not future.done():
                            self._last_ack = datetime.now(UTC).isoformat()
                            future.set_result(data)
                        else:
                            self.counters['late_or_unknown_ack'] += 1
                        continue
                    if str(data.get('self_id') or '') != c.bot:
                        self.counters['identity_rejected'] += 1
                        await c.ws.close(code=1008, reason='identity mismatch')
                        break
                    self._last_event = datetime.now(UTC).isoformat()
                    if data.get('post_type') == 'notice' and data.get('notice_type') in {'bot_status', 'bot_offline'}:
                        if data['notice_type'] == 'bot_offline' or data.get('sub_type') == 'offline':
                            c.online = c.good = False
                        self.counters['status_events'] += 1
                        continue
                    if data.get('post_type') == 'meta_event':
                        # Negative status invalidates readiness immediately. Positive status
                        # still requires an active get_status probe.
                        status = data.get('status') or {}
                        if status.get('online') is False or status.get('good') is False:
                            c.online = c.good = False
                        continue
                    if c is not self._connection or not c.verified or not self.ready:
                        self.counters['unready_event'] += 1
                        continue
                    message = await self.normalize_message(data)
                    if message is not None:
                        try:
                            self._queue.put_nowait((c, message))
                        except asyncio.QueueFull:
                            self.counters['ingress_overflow'] += 1
                            await c.ws.close(code=1013, reason='ingress full; events may be lost')
                            break
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError):
                    self.counters['invalid_frame'] += 1
        except ConnectionClosed:
            pass
        finally:
            c.online = c.good = False
            for future in c.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError('OneBot connection closed'))

    async def _handle_connection(self, ws):
        if len(self._connections) >= 2:
            await ws.close(code=1013, reason='connection bound')
            return
        self._epoch += 1
        c = _Connection(ws, self.config.expected_self_id, self._epoch)
        self._connections.add(c)
        reader = asyncio.create_task(self._read(c))
        try:
            await self._probe(c)
            previous = self._connection
            if reader.done() or (previous and previous.epoch > c.epoch):
                return
            c.verified = True
            self._connection = c
            if previous:
                await previous.ws.close(code=1000, reason='replaced by verified connection')
            while not reader.done():
                done, _ = await asyncio.wait({reader}, timeout=self.config.status_interval_seconds)
                if done:
                    break
                await self._status(c)
        except (ConnectionError, ConnectionClosed, TimeoutError, ValueError, TypeError, AttributeError):
            self.counters['connection_failure'] += 1
        finally:
            if self._connection is c:
                self._connection = None
            await ws.close()
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            self._connections.discard(c)
            self.counters['disconnects'] += 1

    async def _consume(self):
        while True:
            c, message = await self._queue.get()
            try:
                if c is self._connection and c.verified and self._callback:
                    await self._callback(message)
                else:
                    self.counters['stale_ingress'] += 1
            except Exception:
                # Exception messages may contain model/user input; log only a count.
                self.counters['ingress_failure'] += 1
                logger.warning('OneBot ingress callback failed; inspect command state')
            finally:
                self._queue.task_done()

    async def send_with_receipt(self, message):
        c = self._connection
        if not c or str(message.metadata.get('self_id', '')) != c.bot or not self.ready:
            return ChannelSendResult('failed', error_code='not_connected')
        scope = message.metadata.get('detail_type')
        if scope not in {'private', 'group'}:
            return ChannelSendResult('failed', error_code='invalid_target')
        target = message.metadata.get('group_id') if scope == 'group' else message.sender_id
        if not target:
            return ChannelSendResult('failed', error_code='invalid_target')
        segments = message.metadata.get('onebot_segments')
        if segments is None:
            segments = [{'type': 'text', 'data': {'text': message.content}}]
            if message.metadata.get('mention_sender'):
                segments.insert(0, {'type': 'at', 'data': {'qq': message.sender_id}})
        params = {'group_id' if scope == 'group' else 'user_id': target, 'message': segments}
        if scope == 'private' and message.metadata.get('group_id'):
            params['group_id'] = message.metadata['group_id']
        action = f'send_{scope}_msg'
        attempt = ':'.join(str(message.metadata.get(k, 'control')) for k in ('delivery_id', 'msg_seq', 'lease_token'))
        # One outstanding send per bot; acquiring the lock never starts an external effect.
        async with self._send_lock:
            await asyncio.sleep(max(0, self.config.send_interval_seconds - (monotonic() - self._last_send)))
            if c is not self._connection or not self.ready:
                return ChannelSendResult('failed', error_code='not_connected')
            try:
                self._last_send = monotonic()
                response, echo = await self._rpc(c, action, params, attempt)
            except (TimeoutError, ConnectionError, ConnectionClosed, OSError):
                self.counters['uncertain'] += 1
                return ChannelSendResult('uncertain', error_code='response_unknown')
        status, code = response.get('status'), response.get('retcode')
        data = response.get('data') or {}
        mid = data.get('message_id') if isinstance(data, dict) else None
        receipt = {'provider': 'SnowLuma', 'version': c.version, 'action': action,
                   'connection_epoch': c.epoch, 'request_id': echo}
        if self._ok(response) and isinstance(mid, (str, int)) and not isinstance(mid, bool) and str(mid):
            return ChannelSendResult('accepted', str(mid), status, code, receipt=receipt)
        # SnowLuma's implementation errors may follow an external effect. Only
        # its documented pre-execution parameter/unsupported-action errors fail.
        if status == 'failed' and type(code) is int and code in {1400, 1404}:
            return ChannelSendResult('failed', status=status, retcode=code, error_code='provider_rejected', receipt=receipt)
        self.counters['uncertain'] += 1
        return ChannelSendResult('uncertain', status=status, retcode=code, error_code='ambiguous_response', receipt=receipt)

    async def send_message(self, message):
        return (await self.send_with_receipt(message)).accepted

    async def start_monitor(self, callback=None):
        if self._server:
            raise RuntimeError('OneBot listener already started')
        self._token = Path(self.config.access_token_file).read_text().strip()
        if not self._token or not self.config.expected_self_id:
            raise ValueError('OneBot requires a secret token file and expected_self_id')
        self._callback = callback
        self._server = await serve(self._handle_connection, self.config.host, self.config.port,
                                   process_request=self._handshake, max_size=self.config.max_frame_bytes,
                                   logger=transport_logger,
                                   max_queue=16, ping_interval=20, ping_timeout=20,
                                   open_timeout=10, close_timeout=5)
        self._consumer = asyncio.create_task(self._consume())
        self._monitoring = True
        return True

    async def stop_monitor(self):
        self._monitoring = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._consumer:
            self._consumer.cancel()
            await asyncio.gather(self._consumer, return_exceptions=True)
            self._consumer = None
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
        self._callback = None
        self._token = ''
        return True
