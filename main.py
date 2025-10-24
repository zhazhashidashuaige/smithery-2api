import logging
import math
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Request, HTTPException, Depends, Header, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.core.config import settings
from app.providers.smithery_provider import SmitheryProvider
from app.services.metrics_store import metrics_store
from app.services.model_visibility_store import model_visibility_store

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

provider = SmitheryProvider()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"应用启动中... {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info("服务已进入 'Cloudscraper' 模式，将自动处理 Cloudflare 挑战。")
    logger.info(
        "服务将在 http://0.0.0.0:%s 上可用 (可通过 PORT 环境变量覆盖)",
        settings.runtime_port,
    )
    yield
    logger.info("应用关闭。")

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=settings.DESCRIPTION,
    lifespan=lifespan
)


@lru_cache()
def _load_dashboard_html() -> str:
    dashboard_path = Path(__file__).resolve().parent / "app" / "frontend" / "dashboard.html"
    if dashboard_path.exists():
        return dashboard_path.read_text(encoding="utf-8")
    logger.warning("未找到 dashboard.html，返回占位页面。")
    return "<html><body><h1>Dashboard 未找到</h1></body></html>"


def _parse_time_param(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None

    candidate = value.strip()
    if not candidate:
        return None

    try:
        normalized = candidate.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.timestamp()
    except ValueError:
        try:
            return float(candidate)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"无法解析时间参数: {value}") from exc


def _to_iso(timestamp: Optional[float]) -> Optional[str]:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _extract_client_ip(request: Request) -> Optional[str]:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        for part in forwarded_for.split(","):
            candidate = part.strip()
            if candidate:
                return candidate

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        candidate = real_ip.strip()
        if candidate:
            return candidate

    if request.client and request.client.host:
        return request.client.host

    return None

async def verify_api_key(authorization: Optional[str] = Header(None)):
    if settings.API_MASTER_KEY and settings.API_MASTER_KEY != "1":
        if not authorization or "bearer" not in authorization.lower():
            raise HTTPException(status_code=401, detail="需要 Bearer Token 认证。")
        token = authorization.split(" ")[-1]
        if token != settings.API_MASTER_KEY:
            raise HTTPException(status_code=403, detail="无效的 API Key。")

class ModelVisibilityUpdate(BaseModel):
    hidden_models: List[str]


@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
async def chat_completions(request: Request) -> StreamingResponse:
    try:
        request_data = await request.json()
        model = request_data.get("model")
        if isinstance(model, str) and model_visibility_store.is_hidden(model):
            raise HTTPException(status_code=403, detail="该模型暂时被屏蔽")
        client_ip = _extract_client_ip(request)
        return await provider.chat_completion(request_data, client_ip=client_ip)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"处理聊天请求时发生顶层错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")

@app.get("/v1/models", dependencies=[Depends(verify_api_key)], response_class=JSONResponse)
async def list_models():
    return await provider.get_models()


@app.get(
    "/settings/models/visibility",
    dependencies=[Depends(verify_api_key)],
    response_class=JSONResponse,
)
async def get_model_visibility() -> JSONResponse:
    return JSONResponse(content=model_visibility_store.describe())


@app.put(
    "/settings/models/visibility",
    dependencies=[Depends(verify_api_key)],
    response_class=JSONResponse,
)
async def update_model_visibility(payload: ModelVisibilityUpdate) -> JSONResponse:
    hidden = model_visibility_store.set_hidden(payload.hidden_models)
    settings.HIDDEN_MODELS = hidden
    return JSONResponse(content=model_visibility_store.describe())


@app.get("/metrics/requests", dependencies=[Depends(verify_api_key)], response_class=JSONResponse)
async def get_request_metrics(
    start: Optional[str] = Query(None, description="起始时间 (ISO8601 或 UNIX 秒)"),
    end: Optional[str] = Query(None, description="结束时间 (ISO8601 或 UNIX 秒)"),
    model: Optional[str] = Query(None, description="精确匹配的模型名称"),
    limit: int = Query(50, ge=1, le=1000, description="每页条数 (默认 50)"),
    page: int = Query(1, ge=1, description="页码 (从 1 开始)"),
):
    start_ts = _parse_time_param(start)
    end_ts = _parse_time_param(end)

    if start_ts and end_ts and start_ts > end_ts:
        raise HTTPException(status_code=400, detail="起始时间不能晚于结束时间。")

    total_count = metrics_store.count_records(start=start_ts, end=end_ts, model=model)

    page_size = limit
    total_pages = math.ceil(total_count / page_size) if total_count and page_size else 0

    if total_count == 0:
        current_page = 1
        offset = 0
    else:
        current_page = min(page, total_pages) if total_pages else 1
        offset = (current_page - 1) * page_size

    records = metrics_store.list_records(
        start=start_ts,
        end=end_ts,
        model=model,
        limit=page_size,
        offset=offset,
        newest_first=True,
    )

    formatted = []
    for record in records:
        formatted.append({
            "id": record.get("id"),
            "model": record.get("model"),
            "prompt_tokens": record.get("prompt_tokens", 0),
            "completion_tokens": record.get("completion_tokens", 0),
            "total_tokens": record.get("total_tokens", 0),
            "duration_ms": record.get("duration_ms", 0.0),
            "status": record.get("status", "unknown"),
            "error_message": record.get("error_message"),
            "client_ip": record.get("client_ip"),
            "started_at": _to_iso(record.get("started_at")),
            "completed_at": _to_iso(record.get("completed_at")),
        })

    return JSONResponse(
        content={
            "data": formatted,
            "pagination": {
                "total": total_count,
                "page": current_page,
                "page_size": page_size,
                "total_pages": total_pages,
            },
        }
    )


@app.get("/metrics/summary", dependencies=[Depends(verify_api_key)], response_class=JSONResponse)
async def get_metrics_summary(
    start: Optional[str] = Query(None, description="起始时间 (ISO8601 或 UNIX 秒)"),
    end: Optional[str] = Query(None, description="结束时间 (ISO8601 或 UNIX 秒)"),
    model: Optional[str] = Query(None, description="精确匹配的模型名称"),
):
    start_ts = _parse_time_param(start)
    end_ts = _parse_time_param(end)

    if start_ts and end_ts and start_ts > end_ts:
        raise HTTPException(status_code=400, detail="起始时间不能晚于结束时间。")

    summary = metrics_store.summarize_records(start=start_ts, end=end_ts, model=model)
    token_summary = metrics_store.summarize_by_token(start=start_ts, end=end_ts, model=model)

    configured_indexes = set(range(len(settings.AUTH_COOKIES)))
    configured_indexes.update(token_summary.keys())

    token_stats = []
    for index in sorted(configured_indexes):
        token_data = token_summary.get(index, {})
        usage_count = int(token_data.get("usage_count", 0))
        total_tokens = int(token_data.get("total_tokens", 0))
        last_completed = token_data.get("last_completed_at")

        token_label = f"SMITHERY_COOKIE_{index + 1}"
        token_email = None
        if 0 <= index < len(settings.AUTH_COOKIES):
            cookie = settings.AUTH_COOKIES[index]
            token_label = cookie.name or token_label
            token_email = cookie.masked_email

        token_stats.append({
            "token_index": index,
            "token_label": token_label,
            "token_email": token_email,
            "usage_count": usage_count,
            "total_tokens": total_tokens,
            "last_completed_at": _to_iso(last_completed),
        })

    summary.update({
        "window_start": _to_iso(start_ts) if start_ts else None,
        "window_end": _to_iso(end_ts) if end_ts else None,
        "token_stats": token_stats,
    })

    return JSONResponse(content=summary)


@app.get("/dashboard", summary="可视化监控面板", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(content=_load_dashboard_html())


@app.get("/", summary="根路径")
def root():
    return {"message": f"欢迎来到 {settings.APP_NAME} v{settings.APP_VERSION}. 服务运行正常。"}
