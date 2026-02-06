from typing import List

from fastapi import Body, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import ALLOWED_ORIGINS
from .schemas import (
    BoardItem,
    CatalogItem,
    ChartPoint,
    ChartResponse,
    EstimateItem,
    EstimateResponse,
    HoldingItem,
    HoldingProfitItem,
    HoldingProfitResponse,
    FuturesItem,
    MaLineResponse,
    SuggestItem,
    SourceInfo,
)
from .config import TUSHARE_TOKEN, XUEQIU_COOKIE, JOINQUANT_TOKEN, RICEQUANT_TOKEN, AKSHARE_ENABLED
from .services import (
    build_ma_line,
    build_pro_trend,
    compute_profit,
    fetch_boards,
    fetch_estimate,
    fetch_fund_catalog,
    fetch_fund_suggest,
    fetch_futures,
    fetch_nav_history,
    trade_status,
    _breaker,
)
from .scheduler import scheduler, setup_scheduler

app = FastAPI(title="MoneyWatch Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    setup_scheduler()
    scheduler.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    scheduler.shutdown()


@app.get("/api/real-time/estimate", response_model=EstimateResponse)
async def real_time_estimate(
    codes: str = Query(..., description="comma separated codes"),
    source: str | None = Query(None, description="optional source name"),
):
    items: List[EstimateItem] = []
    for code in [c.strip() for c in codes.split(",") if c.strip()]:
        data = await fetch_estimate(code, source=source)
        items.append(
            EstimateItem(
                code=data.get("fundcode", code),
                name=data.get("name", ""),
                gsz=float(data.get("gsz") or 0),
                gszzl=float(data.get("gszzl") or 0),
                gztime=str(data.get("gztime") or ""),
                source=source or "fundgz",
            )
        )
    return EstimateResponse(items=items)


@app.get("/api/chart/pro-trend/{code}", response_model=ChartResponse)
async def pro_trend(code: str):
    data = await build_pro_trend(code)
    points = [ChartPoint(ts=ts, pct=0, nav=nav) for ts, nav in data["points"]]
    ma5 = [ChartPoint(ts=ts, pct=0, nav=nav) for ts, nav in data["ma5"]]
    ma10 = [ChartPoint(ts=ts, pct=0, nav=nav) for ts, nav in data["ma10"]]
    return ChartResponse(code=code, points=points, ma5=ma5, ma10=ma10, last_nav=data["last_nav"])


@app.get("/api/history/ma-line/{code}", response_model=MaLineResponse)
async def ma_line(code: str, source: str | None = Query(None)):
    data = await build_ma_line(code)
    return MaLineResponse(code=code, **data)


@app.get("/api/history/nav/{code}")
async def nav_history(code: str, source: str | None = Query(None)):
    raw = await fetch_nav_history(code, source=source)
    out = []
    from datetime import datetime

    for it in raw:
        if "date" in it and "nav" in it:
            out.append({"date": it["date"], "nav": it["nav"]})
        elif "ts" in it and "nav" in it:
            dt = datetime.fromtimestamp(int(it["ts"]) / 1000).strftime("%Y-%m-%d")
            out.append({"date": dt, "nav": it["nav"]})
    return out


@app.post("/api/hold/profit", response_model=HoldingProfitResponse)
async def hold_profit(items: List[HoldingItem] = Body(...)):
    total_value = 0.0
    total_cost = 0.0
    out: List[HoldingProfitItem] = []

    for it in items:
        est = await fetch_estimate(it.code)
        result = compute_profit(est, it.model_dump())
        total_value += result["currentValue"]
        total_cost += result["totalCost"]
        out.append(
            HoldingProfitItem(
                code=it.code,
                currentValue=result["currentValue"],
                totalCost=result["totalCost"],
                pnl=result["pnl"],
                pnlRate=result["pnlRate"],
            )
        )

    total_pnl = total_value - total_cost
    total_pnl_rate = (total_pnl / total_cost * 100) if total_cost > 0 else None

    return HoldingProfitResponse(
        items=out,
        totalValue=total_value,
        totalCost=total_cost,
        totalPnl=total_pnl,
        totalPnlRate=total_pnl_rate,
    )


@app.get("/api/data/source-list", response_model=List[SourceInfo])
async def source_list():
    sources = [
        ("fundgz", 1, "实时估值"),
        ("akshare", 2, "实时估值(可选)"),
        ("tushare", 3, "历史净值"),
        ("xueqiu", 4, "实时估值"),
        ("eastmoney", 5, "历史净值"),
        ("pingzhong", 6, "历史净值兜底"),
        ("joinquant", 7, "历史净值(可选)"),
        ("ricequant", 8, "历史净值(可选)"),
    ]
    out: List[SourceInfo] = []
    for name, p, msg in sources:
        configured = True
        if name == "tushare" and not TUSHARE_TOKEN:
            configured = False
        if name == "xueqiu" and not XUEQIU_COOKIE:
            configured = False
        if name == "akshare" and not AKSHARE_ENABLED:
            configured = False
        if name == "joinquant" and not JOINQUANT_TOKEN:
            configured = False
        if name == "ricequant" and not RICEQUANT_TOKEN:
            configured = False
        ok = configured and _breaker.allow(name)
        out.append(SourceInfo(name=name, priority=p, ok=ok, message=msg))
    return out


@app.get("/api/trade/status")
async def trade_status_api():
    return trade_status()


@app.get("/api/market/boards", response_model=List[BoardItem])
async def market_boards():
    items = await fetch_boards()
    return [BoardItem(**it) for it in items]


@app.get("/api/market/futures", response_model=List[FuturesItem])
async def market_futures():
    items = await fetch_futures()
    return [FuturesItem(**it) for it in items]


@app.get("/api/fund/suggest", response_model=List[SuggestItem])
async def fund_suggest(query: str = Query(...)):
    items = await fetch_fund_suggest(query)
    return [SuggestItem(**it) for it in items]


@app.get("/api/fund/catalog", response_model=List[CatalogItem])
async def fund_catalog():
    items = await fetch_fund_catalog()
    return [CatalogItem(**it) for it in items]
