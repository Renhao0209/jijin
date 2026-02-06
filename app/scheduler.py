from __future__ import annotations

from datetime import datetime
from typing import List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import WATCH_CODES
from .services import fetch_nav_history, fetch_estimate, _cache


def _is_trading_day(dt: datetime) -> bool:
    # Simple weekday check; exclude weekends.
    return dt.weekday() < 5


scheduler = AsyncIOScheduler()


def _codes() -> List[str]:
    return WATCH_CODES


async def prewarm_sources() -> None:
    if not _is_trading_day(datetime.now()):
        return
    codes = _codes()
    if not codes:
        return
    for code in codes:
        try:
            await fetch_estimate(code)
        except Exception:
            pass


async def refresh_nav_close() -> None:
    if not _is_trading_day(datetime.now()):
        return
    codes = _codes()
    if not codes:
        return
    for code in codes:
        try:
            await fetch_nav_history(code)
        except Exception:
            pass


def cleanup_cache() -> None:
    _cache.clear()


def setup_scheduler() -> None:
    # 交易日 9:00 / 13:00 预热
    scheduler.add_job(prewarm_sources, CronTrigger(hour=9, minute=0))
    scheduler.add_job(prewarm_sources, CronTrigger(hour=13, minute=0))
    # 交易日 20:00 拉净值刷新缓存
    scheduler.add_job(refresh_nav_close, CronTrigger(hour=20, minute=0))
    # 每日 0:00 清理缓存
    scheduler.add_job(cleanup_cache, CronTrigger(hour=0, minute=0))
