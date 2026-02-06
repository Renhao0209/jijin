import json
import re
from typing import Dict, List, Optional
from datetime import datetime

import httpx

from .config import TUSHARE_TOKEN, XUEQIU_COOKIE, AKSHARE_ENABLED


class DataSourceError(Exception):
    pass


class FundGzSource:
    name = "fundgz"

    async def fetch_estimate(self, code: str) -> Dict:
        url = f"https://fundgz.1234567.com.cn/js/{code}.js"
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            raise DataSourceError(f"fundgz http {resp.status_code}")
        text = resp.text
        i = text.find("(")
        j = text.rfind(")")
        if i < 0 or j <= i:
            raise DataSourceError("fundgz jsonp parse error")
        payload = json.loads(text[i + 1 : j])
        return payload


class EastmoneyNavSource:
    name = "eastmoney"

    async def fetch_nav_history(self, code: str, page_size: int = 365) -> List[Dict]:
        url = "https://api.fund.eastmoney.com/f10/lsjz"
        params = {"fundCode": code, "pageIndex": "1", "pageSize": str(page_size)}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            raise DataSourceError(f"eastmoney http {resp.status_code}")
        data = resp.json()
        lst = data.get("Data", {}).get("LSJZList", [])
        out: List[Dict] = []
        for it in lst:
            date_str = (it.get("FSRQ") or "").strip()
            nav_str = (it.get("DWJZ") or "").strip()
            if not date_str or not nav_str:
                continue
            out.append({"date": date_str, "nav": float(nav_str)})
        return out


class PingZhongSource:
    name = "pingzhong"

    async def fetch_nav_history(self, code: str) -> List[Dict]:
        url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            raise DataSourceError(f"pingzhong http {resp.status_code}")
        text = resp.text
        m = re.search(r"var\s+Data_netWorthTrend\s*=\s*(\[[\s\S]*?\]);", text)
        if not m:
            return []
        raw = m.group(1)
        arr = json.loads(raw)
        out: List[Dict] = []
        for it in arr:
            ts = it.get("x")
            nav = it.get("y")
            if isinstance(ts, (int, float)) and isinstance(nav, (int, float)):
                out.append({"ts": int(ts), "nav": float(nav)})
        return out


class XueqiuEstimateSource:
    name = "xueqiu"

    def __init__(self, cookie: str) -> None:
        self.cookie = cookie

    async def fetch_estimate(self, code: str) -> Dict:
        if not self.cookie:
            raise DataSourceError("xueqiu cookie missing")

        # Xueqiu fund symbol format, e.g. F110022
        symbol = f"F{code}"
        url = "https://stock.xueqiu.com/v5/stock/quote.json"
        params = {"symbol": symbol}
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Cookie": self.cookie,
            "Referer": "https://xueqiu.com/",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params, headers=headers)
        if resp.status_code != 200:
            raise DataSourceError(f"xueqiu http {resp.status_code}")
        data = resp.json().get("data", {}) if isinstance(resp.json(), dict) else {}
        quote = data.get("quote", {}) if isinstance(data, dict) else {}
        # Best-effort mapping to fundgz-like fields
        gsz = float(quote.get("current") or 0)
        pct = float(quote.get("percent") or 0)
        name = str(quote.get("name") or "")
        ts = quote.get("timestamp")
        gztime = ""
        if isinstance(ts, (int, float)):
            gztime = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M")
        if gsz <= 0:
            raise DataSourceError("xueqiu empty quote")
        return {
            "fundcode": code,
            "name": name,
            "gsz": gsz,
            "gszzl": pct,
            "gztime": gztime,
        }


class TushareNavSource:
    name = "tushare"

    def __init__(self, token: str) -> None:
        self.token = token

    async def fetch_nav_history(self, code: str, page_size: int = 365) -> List[Dict]:
        if not self.token:
            raise DataSourceError("tushare token missing")

        ts_code = code if code.endswith(".OF") else f"{code}.OF"
        url = "https://api.tushare.pro"
        params = {
            "api_name": "fund_nav",
            "token": self.token,
            "params": {"ts_code": ts_code},
            "fields": "ts_code,nav_date,unit_nav",
        }
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.post(url, json=params, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            raise DataSourceError(f"tushare http {resp.status_code}")
        data = resp.json()
        if not isinstance(data, dict) or data.get("code") not in {0, "0"}:
            raise DataSourceError("tushare response error")
        rows = data.get("data", {}).get("items", []) if isinstance(data.get("data"), dict) else []
        out: List[Dict] = []
        for row in rows[:page_size]:
            if not isinstance(row, list) or len(row) < 3:
                continue
            nav_date = str(row[1])
            unit_nav = row[2]
            try:
                nav = float(unit_nav)
            except Exception:
                continue
            if nav_date:
                out.append({"date": nav_date, "nav": nav})
        return out


class AkShareEstimateSource:
    name = "akshare"

    async def fetch_estimate(self, code: str) -> Dict:
        if not AKSHARE_ENABLED:
            raise DataSourceError("akshare disabled")
        try:
            import akshare as ak  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise DataSourceError("akshare not installed") from exc

        # Best-effort: akshare fund open fund daily estimate may vary by version.
        try:
            df = ak.fund_etf_fund_info_em(fund=code)
            if df is None or df.empty:
                raise DataSourceError("akshare empty")
            latest = df.iloc[0]
            gsz = float(latest.get("最新价", 0))
            pct = float(latest.get("涨跌幅", 0))
            name = str(latest.get("名称", ""))
            return {"fundcode": code, "name": name, "gsz": gsz, "gszzl": pct, "gztime": ""}
        except Exception as exc:  # noqa: BLE001
            raise DataSourceError("akshare fetch error") from exc


def build_sources() -> Dict[str, object]:
    sources: Dict[str, object] = {
        "fundgz": FundGzSource(),
        "eastmoney": EastmoneyNavSource(),
        "pingzhong": PingZhongSource(),
    }
    if XUEQIU_COOKIE:
        sources["xueqiu"] = XueqiuEstimateSource(XUEQIU_COOKIE)
    if TUSHARE_TOKEN:
        sources["tushare"] = TushareNavSource(TUSHARE_TOKEN)
    if AKSHARE_ENABLED:
        sources["akshare"] = AkShareEstimateSource()
    return sources
