from __future__ import annotations

from datetime import datetime, time
from typing import Dict, List, Optional, Tuple

import httpx

from .cache import TTLCache
from .config import CACHE_TTL_EST, CACHE_TTL_NAV
from .sources import DataSourceError, build_sources


_sources = build_sources()
_cache = TTLCache()


class _CircuitBreaker:
    def __init__(self, fail_threshold: int = 3, cool_down_sec: int = 300) -> None:
        self.fail_threshold = fail_threshold
        self.cool_down_sec = cool_down_sec
        self._fail_count: Dict[str, int] = {}
        self._opened_at: Dict[str, float] = {}

    def allow(self, name: str) -> bool:
        import time

        opened = self._opened_at.get(name)
        if opened is None:
            return True
        if time.time() - opened >= self.cool_down_sec:
            self._opened_at.pop(name, None)
            self._fail_count.pop(name, None)
            return True
        return False

    def on_success(self, name: str) -> None:
        self._fail_count.pop(name, None)
        self._opened_at.pop(name, None)

    def on_failure(self, name: str) -> None:
        n = self._fail_count.get(name, 0) + 1
        self._fail_count[name] = n
        if n >= self.fail_threshold:
            import time

            self._opened_at[name] = time.time()


_breaker = _CircuitBreaker()


def _is_trading_time(dt: datetime) -> bool:
    t = dt.time()
    return time(9, 30) <= t <= time(15, 0)


def _ma(values: List[float], window: int) -> Optional[float]:
    if len(values) < window:
        return None
    return sum(values[:window]) / window


def _calc_ma_series(points: List[Tuple[int, float]], window: int) -> List[Tuple[int, float]]:
    out: List[Tuple[int, float]] = []
    if len(points) < window:
        return out
    for i in range(window - 1, len(points)):
        s = 0.0
        for j in range(i - window + 1, i + 1):
            s += points[j][1]
        out.append((points[i][0], s / window))
    return out


async def fetch_estimate(code: str, source: str | None = None) -> Dict:
    src_name = source or "auto"
    key = f"est::{src_name}::{code}"
    cached = _cache.get(key)
    if cached:
        return cached

    est_sources = [
        s for s in ["fundgz", "xueqiu", "akshare"] if s in _sources
    ]

    if source and source in est_sources:
        est_sources = [source]

    last_err: Optional[Exception] = None
    for name in est_sources:
        if not _breaker.allow(name):
            continue
        src = _sources.get(name)
        if src is None:
            continue
        try:
            payload = await src.fetch_estimate(code)
            _breaker.on_success(name)
            _cache.set(key, payload, CACHE_TTL_EST)
            return payload
        except Exception as e:  # noqa: BLE001
            last_err = e if isinstance(e, Exception) else Exception(str(e))
            _breaker.on_failure(name)

    if last_err is not None:
        raise last_err
    raise DataSourceError("no available estimate source")


async def fetch_nav_history(code: str, source: str | None = None) -> List[Dict]:
    key = f"nav::{code}"
    cached = _cache.get(key)
    if cached:
        return cached

    nav_sources = [
        s for s in ["eastmoney", "pingzhong", "tushare"] if s in _sources
    ]
    if source and source in nav_sources:
        nav_sources = [source]

    for name in nav_sources:
        if not _breaker.allow(name):
            continue
        src = _sources.get(name)
        if src is None:
            continue
        try:
            lst = await src.fetch_nav_history(code)
            if lst:
                _breaker.on_success(name)
                _cache.set(key, lst, CACHE_TTL_NAV)
                return lst
        except Exception:
            _breaker.on_failure(name)

    return []


async def fetch_boards() -> List[Dict]:
    key = "boards"
    cached = _cache.get(key)
    if cached:
        return cached

    params = {
        "pn": "1",
        "pz": "20",
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fs": "m:90+t:2",
        "fields": "f12,f14,f3,f2",
    }
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
    if resp.status_code != 200:
        return []
    data = resp.json().get("data", {})
    diff = data.get("diff", []) if isinstance(data, dict) else []
    out: List[Dict] = []
    for it in diff:
        code = str(it.get("f12") or "")
        name = str(it.get("f14") or "")
        pct = float(it.get("f3") or 0)
        value = float(it.get("f2") or 0)
        if code and name:
            out.append({"code": code, "name": name, "pct": pct, "value": value})
    _cache.set(key, out, 60)
    return out


async def fetch_futures() -> List[Dict]:
    key = "futures"
    cached = _cache.get(key)
    if cached:
        return cached

    fs_list = ["m:113+t:2", "m:113+t:3", "m:114+t:2"]
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    out: List[Dict] = []
    seen = set()
    async with httpx.AsyncClient(timeout=10) as client:
        for fs in fs_list:
            params = {
                "pn": "1",
                "pz": "50",
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fs": fs,
                "fields": "f12,f14,f3,f2",
            }
            resp = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                continue
            data = resp.json().get("data", {})
            diff = data.get("diff", []) if isinstance(data, dict) else []
            for it in diff:
                code = str(it.get("f12") or "")
                if not code or code in seen:
                    continue
                name = str(it.get("f14") or "")
                pct = float(it.get("f3") or 0)
                value = float(it.get("f2") or 0)
                seen.add(code)
                out.append({"code": code, "name": name, "pct": pct, "value": value})
            if len(out) >= 12:
                break
    _cache.set(key, out, 60)
    return out


async def fetch_fund_suggest(query: str) -> List[Dict]:
    q = query.strip()
    if not q:
        return []
    key = f"suggest::{q}"
    cached = _cache.get(key)
    if cached:
        return cached
    url = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
    params = {"m": "1", "key": q}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
    if resp.status_code != 200:
        return []
    data = resp.json()
    datas = data.get("Datas", []) if isinstance(data, dict) else []
    out: List[Dict] = []
    for it in datas:
        code = str(it.get("CODE") or it.get("Code") or it.get("_id") or "")
        if not code:
            continue
        name = str(it.get("NAME") or it.get("Name") or "")
        pinyin = str(it.get("PINYIN") or it.get("Pinyin") or "")
        out.append({"code": code, "name": name, "pinyin": pinyin})
        if len(out) >= 50:
            break
    _cache.set(key, out, 3600)
    return out


async def fetch_fund_catalog() -> List[Dict]:
    key = "fund_catalog"
    cached = _cache.get(key)
    if cached:
        return cached

    url = "https://fund.eastmoney.com/js/fundcode_search.js"
    async with httpx.AsyncClient(timeout=12) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
    if resp.status_code != 200:
        return []
    text = resp.text
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return []
    import json

    arr = json.loads(text[start : end + 1])
    out: List[Dict] = []
    for it in arr:
        if not isinstance(it, list) or len(it) < 3:
            continue
        code = str(it[0])
        pinyin = str(it[1])
        name = str(it[2])
        if code and name:
            out.append({"code": code, "name": name, "pinyin": pinyin})
    _cache.set(key, out, 86400)
    return out


async def build_ma_line(code: str) -> Dict:
    hist = await fetch_nav_history(code)
    values = [float(it["nav"]) for it in hist if "nav" in it]
    return {
        "ma10": _ma(values, 10),
        "ma30": _ma(values, 30),
        "ma60": _ma(values, 60),
    }


async def build_pro_trend(code: str) -> Dict:
    hist = await fetch_nav_history(code)
    if not hist:
        return {"points": [], "ma5": [], "ma10": [], "last_nav": None}

    points: List[Tuple[int, float]] = []
    last_nav = None
    for it in reversed(hist[:240]):
        if "date" in it and "nav" in it:
            dt = datetime.fromisoformat(it["date"]).strftime("%Y-%m-%d")
            ts = int(datetime.fromisoformat(dt).timestamp() * 1000)
            nav = float(it["nav"])
            points.append((ts, nav))
            last_nav = nav
        elif "ts" in it and "nav" in it:
            ts = int(it["ts"])
            nav = float(it["nav"])
            points.append((ts, nav))
            last_nav = nav

    ma5 = _calc_ma_series(points, 5)
    ma10 = _calc_ma_series(points, 10)
    return {"points": points, "ma5": ma5, "ma10": ma10, "last_nav": last_nav}


def compute_profit(estimate: Dict, holding: Dict) -> Dict:
    gsz = float(estimate.get("gsz") or 0)
    shares = float(holding.get("shares") or 0)
    cost_price = float(holding.get("costPrice") or 0)
    amount = float(holding.get("amount") or 0)
    cost = float(holding.get("cost") or 0)

    if shares > 0 and gsz > 0:
        current = shares * gsz
    else:
        current = amount

    total_cost = shares * cost_price if shares > 0 and cost_price > 0 else cost
    pnl = current - total_cost
    pnl_rate = (pnl / total_cost * 100) if total_cost > 0 else None

    return {
        "currentValue": current,
        "totalCost": total_cost,
        "pnl": pnl,
        "pnlRate": pnl_rate,
    }


def trade_status() -> Dict:
    now = datetime.now()
    return {"trading": _is_trading_time(now), "time": now.strftime("%H:%M:%S")}
