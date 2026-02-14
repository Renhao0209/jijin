from typing import List
import math

from fastapi import Body, FastAPI, File, Header, Query, UploadFile
import base64
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
    HoldingImportItem,
    HoldingImportImageBase64Request,
    HoldingImportResponse,
    HoldingProfitItem,
    HoldingProfitResponse,
    FuturesItem,
    MaLineResponse,
    SuggestItem,
    SourceInfo,
    SourceVerifyRequest,
    SourceVerifyResponse,
    AiVerifyResponse,
)
from .importer import (
    _ai_error_message,
    _normalize_ai_endpoint,
    ai_extract_holdings_from_image_base64,
    ai_extract_holdings_from_ocr_lines,
    ocr_holdings_from_image_bytes,
    ocr_lines_from_image_bytes,
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


def _normalize_import_items(raw_items: list[dict]) -> list[HoldingImportItem]:
    out: list[HoldingImportItem] = []
    seen: set[str] = set()
    for it in raw_items or []:
        try:
            code = f"{(it or {}).get('code') or ''}".strip()
            if not code or code in seen:
                continue
            name = f"{(it or {}).get('name') or ''}".strip()
            amount = float((it or {}).get('amount') or 0)
            if (not math.isfinite(amount)) or amount <= 0:
                continue
            seen.add(code)
            out.append(HoldingImportItem(code=code, name=name, amount=amount))
        except Exception:
            continue
    return out


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
    x_mw_xueqiu_cookie: str | None = Header(None),
    x_mw_akshare_enabled: str | None = Header(None),
):
    akshare_enabled = (x_mw_akshare_enabled or "").strip() in {"1", "true", "True"}
    items: List[EstimateItem] = []
    for code in [c.strip() for c in codes.split(",") if c.strip()]:
        data = await fetch_estimate(
            code,
            source=source,
            xueqiu_cookie=x_mw_xueqiu_cookie,
            akshare_enabled=akshare_enabled,
        )
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
async def pro_trend(
    code: str,
    source: str | None = Query(None),
    x_mw_tushare_token: str | None = Header(None),
):
    data = await build_pro_trend(code, source=source, tushare_token=x_mw_tushare_token)
    points = [ChartPoint(ts=ts, pct=0, nav=nav) for ts, nav in data["points"]]
    ma5 = [ChartPoint(ts=ts, pct=0, nav=nav) for ts, nav in data["ma5"]]
    ma10 = [ChartPoint(ts=ts, pct=0, nav=nav) for ts, nav in data["ma10"]]
    return ChartResponse(code=code, points=points, ma5=ma5, ma10=ma10, last_nav=data["last_nav"])


@app.get("/api/history/ma-line/{code}", response_model=MaLineResponse)
async def ma_line(
    code: str,
    source: str | None = Query(None),
    x_mw_tushare_token: str | None = Header(None),
):
    data = await build_ma_line(code, source=source, tushare_token=x_mw_tushare_token)
    return MaLineResponse(code=code, **data)


@app.get("/api/history/nav/{code}")
async def nav_history(
    code: str,
    source: str | None = Query(None),
    x_mw_tushare_token: str | None = Header(None),
):
    raw = await fetch_nav_history(code, source=source, tushare_token=x_mw_tushare_token)
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
async def hold_profit(
    items: List[HoldingItem] = Body(...),
    source: str | None = Query(None),
    x_mw_xueqiu_cookie: str | None = Header(None),
    x_mw_akshare_enabled: str | None = Header(None),
):
    akshare_enabled = (x_mw_akshare_enabled or "").strip() in {"1", "true", "True"}
    total_value = 0.0
    total_cost = 0.0
    out: List[HoldingProfitItem] = []

    for it in items:
        est = await fetch_estimate(
            it.code,
            source=source,
            xueqiu_cookie=x_mw_xueqiu_cookie,
            akshare_enabled=akshare_enabled,
        )
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
async def source_list(
    x_mw_tushare_token: str | None = Header(None),
    x_mw_xueqiu_cookie: str | None = Header(None),
    x_mw_akshare_enabled: str | None = Header(None),
):
    akshare_enabled = (x_mw_akshare_enabled or "").strip() in {"1", "true", "True"}
    sources = [
        ("fundgz", 1, "实时估值（免 token）"),
        ("xueqiu", 2, "实时估值（需 XUEQIU_COOKIE）"),
        ("akshare", 3, "实时估值（可选：需 AKSHARE_ENABLED=1 且安装 akshare）"),
        ("eastmoney", 4, "历史净值（免 token）"),
        ("pingzhong", 5, "历史净值兜底（免 token）"),
        ("tushare", 6, "历史净值（可选：需 TUSHARE_TOKEN）"),
        ("joinquant", 7, "占位：未接入"),
        ("ricequant", 8, "占位：未接入"),
    ]
    out: List[SourceInfo] = []
    for name, p, msg in sources:
        implemented = name in {"fundgz", "eastmoney", "pingzhong", "xueqiu", "tushare", "akshare"}
        configured = True
        if name == "tushare" and not ((x_mw_tushare_token or "").strip() or TUSHARE_TOKEN):
            configured = False
        if name == "xueqiu" and not ((x_mw_xueqiu_cookie or "").strip() or XUEQIU_COOKIE):
            configured = False
        if name == "akshare":
            if not (AKSHARE_ENABLED or akshare_enabled):
                configured = False
            else:
                try:
                    import akshare  # type: ignore  # noqa: F401
                except Exception:
                    configured = False

        # Placeholders: keep them visible but never mark ok.
        if name in {"joinquant", "ricequant"}:
            implemented = False
            configured = False

        ok = implemented and configured and _breaker.allow(name)
        out.append(SourceInfo(name=name, priority=p, ok=ok, message=msg))
    return out


@app.post("/api/data/source-verify", response_model=SourceVerifyResponse)
async def source_verify(payload: SourceVerifyRequest):
    source = payload.source.strip().lower()
    try:
        if source == "xueqiu":
            await fetch_estimate(
                "110022",
                source="xueqiu",
                xueqiu_cookie=payload.xueqiuCookie,
                akshare_enabled=False,
            )
        elif source == "tushare":
            nav = await fetch_nav_history("110022", source="tushare", tushare_token=payload.tushareToken)
            if not nav:
                return SourceVerifyResponse(ok=False, message="tushare 无返回数据")
        elif source == "akshare":
            await fetch_estimate("159915", source="akshare", akshare_enabled=payload.akshareEnabled)
        elif source in {"fundgz", "eastmoney", "pingzhong"}:
            return SourceVerifyResponse(ok=True, message="免配置数据源")
        else:
            return SourceVerifyResponse(ok=False, message="不支持或未接入的数据源")
    except Exception as e:  # noqa: BLE001
        return SourceVerifyResponse(ok=False, message=str(e))
    return SourceVerifyResponse(ok=True, message="验证通过")


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


@app.post("/api/holdings/import-image", response_model=HoldingImportResponse)
async def import_holdings_image(file: UploadFile = File(...)):
    try:
        content = await file.read()
        suffix = ".jpg"
        if file.filename and "." in file.filename:
            suffix = "." + file.filename.split(".")[-1]
        items = ocr_holdings_from_image_bytes(content, suffix=suffix)
        out = _normalize_import_items(items)
        return HoldingImportResponse(ok=True, message="", items=out)
    except Exception as e:  # noqa: BLE001
        return HoldingImportResponse(ok=False, message=f"图片导入失败: {e}", items=[])


@app.post("/api/holdings/import-image-base64", response_model=HoldingImportResponse)
async def import_holdings_image_base64(
    payload: HoldingImportImageBase64Request,
    x_mw_ai_endpoint: str | None = Header(None),
    x_mw_ai_key: str | None = Header(None),
    x_mw_ai_model: str | None = Header(None),
):
    try:
        ext = (payload.fileExt or "jpg").strip().lower().replace(".", "")
        if not ext:
            ext = "jpg"
        raw = payload.imageBase64 or ""
        if "," in raw and "base64" in raw[:40]:
            raw = raw.split(",", 1)[1]

        items = []
        if payload.useAi:
            ep = (x_mw_ai_endpoint or "").strip()
            key = (x_mw_ai_key or "").strip()
            if not ep or not key:
                return HoldingImportResponse(ok=False, message="AI配置不完整，请先在小程序设置中填写 endpoint/apiKey", items=[])
            try:
                items = ai_extract_holdings_from_image_base64(
                    raw,
                    endpoint=ep,
                    api_key=key,
                    model=(x_mw_ai_model or "gpt-4o-mini").strip(),
                    file_ext=ext,
                )
            except Exception as e:  # noqa: BLE001
                return HoldingImportResponse(ok=False, message=f"AI识别失败: {e}", items=[])
        else:
            try:
                content = base64.b64decode(raw)
            except Exception:
                return HoldingImportResponse(ok=False, message="imageBase64 不是有效的 base64", items=[])

            lines = ocr_lines_from_image_bytes(content, suffix=f".{ext}")
            if (x_mw_ai_endpoint or "").strip() and (x_mw_ai_key or "").strip():
                try:
                    items = ai_extract_holdings_from_ocr_lines(
                        lines,
                        endpoint=(x_mw_ai_endpoint or "").strip(),
                        api_key=(x_mw_ai_key or "").strip(),
                        model=(x_mw_ai_model or "gpt-4o-mini").strip(),
                    )
                except Exception:
                    items = []

            if not items:
                from .importer import parse_holdings_from_ocr_lines

                items = parse_holdings_from_ocr_lines(lines)

        out = _normalize_import_items(items)
        return HoldingImportResponse(ok=True, message="", items=out)
    except Exception as e:  # noqa: BLE001
        return HoldingImportResponse(ok=False, message=f"图片导入失败: {e}", items=[])


@app.get("/api/data/ai-verify", response_model=AiVerifyResponse)
async def ai_verify(
    x_mw_ai_endpoint: str | None = Header(None),
    x_mw_ai_key: str | None = Header(None),
    x_mw_ai_model: str | None = Header(None),
):
    ep = _normalize_ai_endpoint((x_mw_ai_endpoint or "").strip())
    key = (x_mw_ai_key or "").strip()
    model = (x_mw_ai_model or "gpt-4o-mini").strip()
    if not ep or not key:
        return AiVerifyResponse(ok=False, message="AI配置不完整")
    try:
        import httpx

        body = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": "你是一个助手"},
                {"role": "user", "content": "仅回复OK"},
            ],
            "max_tokens": 16,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                ep,
                json=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
        if resp.status_code < 200 or resp.status_code >= 300:
            return AiVerifyResponse(ok=False, message=_ai_error_message(resp))
    except Exception as e:  # noqa: BLE001
        return AiVerifyResponse(ok=False, message=str(e))
    return AiVerifyResponse(ok=True, message="AI配置可用")
