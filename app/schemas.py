from typing import List, Optional
from pydantic import BaseModel, Field


class EstimateItem(BaseModel):
    code: str
    name: str = ""
    gsz: float = 0
    gszzl: float = 0
    gztime: str = ""
    source: str = ""


class EstimateResponse(BaseModel):
    items: List[EstimateItem]


class ChartPoint(BaseModel):
    ts: int = Field(..., description="timestamp ms")
    pct: float
    nav: Optional[float] = None


class ChartResponse(BaseModel):
    code: str
    name: str = ""
    points: List[ChartPoint]
    ma5: List[ChartPoint] = []
    ma10: List[ChartPoint] = []
    last_nav: Optional[float] = None


class MaLineResponse(BaseModel):
    code: str
    ma10: Optional[float] = None
    ma30: Optional[float] = None
    ma60: Optional[float] = None


class HoldingItem(BaseModel):
    code: str
    shares: float = 0
    costPrice: float = 0
    amount: float = 0
    cost: float = 0


class HoldingProfitItem(BaseModel):
    code: str
    currentValue: float
    totalCost: float
    pnl: float
    pnlRate: Optional[float] = None


class HoldingProfitResponse(BaseModel):
    items: List[HoldingProfitItem]
    totalValue: float
    totalCost: float
    totalPnl: float
    totalPnlRate: Optional[float] = None


class SourceInfo(BaseModel):
    name: str
    priority: int
    ok: bool
    message: str = ""


class BoardItem(BaseModel):
    code: str
    name: str
    pct: float
    value: float


class FuturesItem(BaseModel):
    code: str
    name: str
    pct: float
    value: float


class SuggestItem(BaseModel):
    code: str
    name: str
    pinyin: str = ""


class CatalogItem(BaseModel):
    code: str
    name: str
    pinyin: str = ""
