import asyncio
from datetime import datetime
from collections.abc import Callable
from .config import Config


def remaining_seconds(target: datetime, now: datetime) -> float:
    return max(0.0, target.timestamp() - now.timestamp())


async def wait_for_start(config: Config, report: Callable[[str], None]) -> None:
    now = datetime.now().astimezone()
    report(f'当前时间：{now:%Y-%m-%d %H:%M:%S %z}；计划开始：{config.start_time:%Y-%m-%d %H:%M:%S %z}')
    if config.start_immediately:
        report('start_immediately=true，立即执行')
        return
    if now >= config.start_time:
        report('已超过计划时间，默认立即执行')
    while (seconds := remaining_seconds(config.start_time, datetime.now().astimezone())) > 0:
        whole = int(seconds)
        report(f'[等待执行] 距离开始 {whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}')
        await asyncio.sleep(min(30, seconds))
