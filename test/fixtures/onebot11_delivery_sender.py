"""SIGKILL harness: real OneBot ack, then hold before PostgreSQL finish."""
import asyncio
import json
import os
import threading

from application.qq_delivery import QQDeliveryAdapter
from channels.onebot11 import OneBot11Channel
from channels.router import ChannelRouter
from common.onebot import OneBot11Config
from common.types import ChannelType
from delivery.service import DeliveryService


async def main():
    channel = OneBot11Channel(OneBot11Config(
        host='127.0.0.1', port=0, expected_self_id='999',
        access_token_file=os.environ['ONEBOT_TEST_TOKEN_FILE'], ack_timeout_seconds=3,
        status_interval_seconds=10, status_stale_seconds=30, send_interval_seconds=0))
    await channel.start_monitor()
    print(json.dumps({'port': channel._server.sockets[0].getsockname()[1]}), flush=True)
    try:
        async with asyncio.timeout(20):
            while not channel.ready:
                await asyncio.sleep(.01)
            router = ChannelRouter()
            router.register(ChannelType.ONEBOT11, channel)
            service = DeliveryService(os.environ['WORKER_DATABASE_URL'], QQDeliveryAdapter(router))

            def hold_finish(row, state, part, receipt=None, error=None):
                assert state == 'pending' and part == 1
                assert receipt['parts'][0]['provider_message_id'] == '-712'
                print('PROVIDER_ACK_BEFORE_PG_FINISH', flush=True)
                threading.Event().wait(30)
                raise RuntimeError('kill harness deadline')

            service.finish = hold_finish
            await service.deliver_once()
    finally:
        await channel.stop_monitor()


if __name__ == '__main__':
    asyncio.run(main())
