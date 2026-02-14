from __future__ import annotations

import json
import math
import re
import tempfile
from pathlib import Path
from typing import Dict, List

import httpx


def _normalize_ai_endpoint(endpoint: str) -> str:
    ep = (endpoint or "").strip().rstrip("/")
    if not ep:
        return ""
    if ep.endswith("/chat/completions"):
        return ep
    if ep.endswith("/v1"):
        return f"{ep}/chat/completions"
    return f"{ep}/v1/chat/completions"


def _ai_error_message(resp: httpx.Response) -> str:
    msg = ""
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                msg = str(err.get("message") or err.get("type") or "").strip()
            if not msg:
                msg = str(data.get("message") or data.get("detail") or "").strip()
    except Exception:
        msg = ""
    if not msg:
        try:
            msg = (resp.text or "").strip().replace("\n", " ")[:220]
        except Exception:
            msg = ""
    return f"AI接口错误: {resp.status_code}" + (f" - {msg}" if msg else "")


def _extract_amount(text: str) -> float | None:
    t = text.replace(",", "").replace("，", "")
    m = re.search(r"(?:¥|￥)?\s*(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except Exception:
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def _extract_code(text: str) -> str:
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    return m.group(1) if m else ""


def parse_holdings_from_ocr_lines(lines: List[str]) -> List[Dict]:
    out: List[Dict] = []
    used = set()

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        code = _extract_code(line)
        amount = _extract_amount(line)

        if not code:
            for j in range(max(0, i - 2), min(len(lines), i + 3)):
                code = _extract_code(lines[j])
                if code:
                    break

        if amount is None:
            for j in range(max(0, i - 2), min(len(lines), i + 3)):
                amount = _extract_amount(lines[j])
                if amount is not None:
                    break

        if not code or amount is None:
            continue

        if code in used:
            continue
        used.add(code)

        name = re.sub(r"\s+", "", re.sub(r"[0-9¥￥.,，]+", "", line)).strip()
        out.append({"code": code, "name": name, "amount": amount})

        if len(out) >= 100:
            break

    return out


def _extract_json_array(text: str) -> str | None:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"```\s*$", "", s)
        s = s.strip()
    i = s.find("[")
    j = s.rfind("]")
    if i >= 0 and j > i:
        return s[i : j + 1]
    return None


def ai_extract_holdings_from_ocr_lines(
    lines: List[str],
    *,
    endpoint: str,
    api_key: str,
    model: str,
) -> List[Dict]:
    ep = _normalize_ai_endpoint(endpoint)
    key = api_key.strip()
    m = model.strip() or "gpt-4o-mini"
    if not ep or not key:
        raise RuntimeError("AI 配置不完整")

    prompt = (
        "你是一个信息抽取器。请从OCR文本中提取持仓列表。\n"
        "只输出严格JSON数组，不要解释。\n"
        "每项字段: code(6位字符串), name(字符串), amount(数字，单位元)。\n"
        "不要把收益/涨跌幅/占比/份额当作amount。\n"
        "OCR文本:\n" + "\n".join(lines)
    )

    body = {
        "model": m,
        "stream": False,
        "temperature": 0,
        "max_tokens": 800,
        "messages": [
            {"role": "system", "content": "只输出JSON数组"},
            {"role": "user", "content": prompt},
        ],
    }

    with httpx.Client(timeout=25) as client:
        resp = client.post(
            ep,
            json=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(_ai_error_message(resp))
    data = resp.json()
    content = ""
    try:
        content = str(data.get("choices", [])[0].get("message", {}).get("content", ""))
    except Exception:
        content = ""
    if not content:
        raise RuntimeError("AI未返回可解析内容")

    arr_text = _extract_json_array(content) or content.strip()
    try:
        arr = json.loads(arr_text)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("AI返回不是有效JSON数组") from exc
    if not isinstance(arr, list):
        raise RuntimeError("AI返回结构异常")

    out: List[Dict] = []
    seen = set()
    for it in arr:
        if not isinstance(it, dict):
            continue
        code = _extract_code(str(it.get("code") or ""))
        if not code or code in seen:
            continue
        seen.add(code)
        name = str(it.get("name") or "").strip()
        try:
            amount = float(it.get("amount") or 0)
        except Exception:
            amount = 0
        if (not math.isfinite(amount)) or amount <= 0:
            continue
        out.append({"code": code, "name": name, "amount": amount})
    return out


def ai_extract_holdings_from_image_base64(
    image_base64: str,
    *,
    endpoint: str,
    api_key: str,
    model: str,
    file_ext: str = "jpg",
) -> List[Dict]:
    ep = _normalize_ai_endpoint(endpoint)
    key = api_key.strip()
    m = model.strip() or "gpt-4o-mini"
    if not ep or not key:
        raise RuntimeError("AI 配置不完整")

    raw = (image_base64 or "").strip()
    if not raw:
        raise RuntimeError("图片为空")
    if "," in raw and "base64" in raw[:40]:
        raw = raw.split(",", 1)[1]

    ext = (file_ext or "jpg").lower().replace(".", "")
    if ext == "jpg":
        ext = "jpeg"
    data_uri = f"data:image/{ext};base64,{raw}"

    prompt = (
        "你是一个信息抽取器。请从截图中提取基金持仓列表。"
        "只输出严格JSON数组，不要解释。"
        "每项字段: code(6位字符串), name(字符串), amount(数字，单位元)。"
        "不要把收益/涨跌幅/占比/份额当作amount。"
    )

    body = {
        "model": m,
        "stream": False,
        "temperature": 0,
        "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": "只输出JSON数组"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
    }

    with httpx.Client(timeout=35) as client:
        resp = client.post(
            ep,
            json=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(_ai_error_message(resp))
    data = resp.json()
    content = ""
    try:
        content = str(data.get("choices", [])[0].get("message", {}).get("content", ""))
    except Exception:
        content = ""
    if not content:
        raise RuntimeError("AI未返回可解析内容")

    arr_text = _extract_json_array(content) or content.strip()
    try:
        arr = json.loads(arr_text)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("AI返回不是有效JSON数组") from exc
    if not isinstance(arr, list):
        raise RuntimeError("AI返回结构异常")

    out: List[Dict] = []
    seen = set()
    for it in arr:
        if not isinstance(it, dict):
            continue
        code = _extract_code(str(it.get("code") or ""))
        if not code or code in seen:
            continue
        seen.add(code)
        name = str(it.get("name") or "").strip()
        try:
            amount = float(it.get("amount") or 0)
        except Exception:
            amount = 0
        if (not math.isfinite(amount)) or amount <= 0:
            continue
        out.append({"code": code, "name": name, "amount": amount})
    return out


def ocr_lines_from_image_bytes(content: bytes, suffix: str = ".jpg") -> List[str]:
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("服务端未安装 rapidocr_onnxruntime，无法图片导入") from exc

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as fp:
        fp.write(content)
        tmp_path = fp.name

    try:
        engine = RapidOCR()
        result, _ = engine(tmp_path)
        lines: List[str] = []
        if isinstance(result, list):
            for it in result:
                if not isinstance(it, list) or len(it) < 2:
                    continue
                txt_obj = it[1]
                text = txt_obj if isinstance(txt_obj, str) else str(txt_obj)
                if text.strip():
                    lines.append(text.strip())
        return lines
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def ocr_holdings_from_image_bytes(content: bytes, suffix: str = ".jpg") -> List[Dict]:
    lines = ocr_lines_from_image_bytes(content, suffix=suffix)
    return parse_holdings_from_ocr_lines(lines)
