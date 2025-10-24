import asyncio
import json
import time
import logging
import uuid
import cloudscraper
from typing import Dict, Any, AsyncGenerator, List, Optional, Tuple

from fastapi.responses import StreamingResponse, JSONResponse

from app.core.config import settings
from app.providers.base_provider import BaseProvider
# 移除了不再使用的 SessionManager
# from app.services.session_manager import SessionManager
from app.services.metrics_store import metrics_store
from app.services.model_visibility_store import model_visibility_store
from app.utils.sse_utils import create_sse_data, create_chat_completion_chunk, DONE_CHUNK
from app.utils.token_counter import estimate_tokens_for_messages, estimate_tokens_for_text

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - [%(levelname)s] - %(message)s')
logger = logging.getLogger(__name__)

class SmitheryProvider(BaseProvider):
    def __init__(self):
        # self.session_manager = SessionManager() # 移除会话管理器
        self.scraper = cloudscraper.create_scraper()
        self.cookie_index = 0

    def _get_cookie(self) -> Tuple[str, int]:
        """从配置中轮换获取一个格式正确的 Cookie 字符串及其索引。"""
        token_index = self.cookie_index
        auth_cookie_obj = settings.AUTH_COOKIES[token_index]
        self.cookie_index = (self.cookie_index + 1) % len(settings.AUTH_COOKIES)
        return auth_cookie_obj.header_cookie_string, token_index

    def _convert_messages_to_smithery_format(self, openai_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        将客户端发来的 OpenAI 格式消息列表转换为 Smithery.ai 后端所需的格式。
        例如: {"role": "user", "content": "你好"} -> {"role": "user", "parts": [{"type": "text", "text": "你好"}]}
        """
        smithery_messages = []
        for msg in openai_messages:
            role = msg.get("role")
            content = msg.get("content", "")
            
            # 忽略格式不正确或内容为空的消息
            if not role or not isinstance(content, str):
                continue
                
            smithery_messages.append({
                "role": role,
                "parts": [{"type": "text", "text": content}],
                "id": f"msg-{uuid.uuid4().hex[:16]}"
            })
        return smithery_messages

    async def chat_completion(
        self,
        request_data: Dict[str, Any],
        client_ip: Optional[str] = None,
    ) -> StreamingResponse:
        """
        处理聊天补全请求。
        此实现为无状态模式，完全依赖客户端发送的完整对话历史。
        """
        
        # 1. 直接从客户端请求中获取完整的消息历史
        messages_from_client = request_data.get("messages", [])
        if not isinstance(messages_from_client, list):
            messages_from_client = []
        
        # 2. 将其转换为 Smithery.ai 后端所需的格式
        smithery_formatted_messages = self._convert_messages_to_smithery_format(messages_from_client)

        model = request_data.get("model", "claude-haiku-4.5")
        request_id = f"chatcmpl-{uuid.uuid4()}"
        prompt_tokens = estimate_tokens_for_messages(messages_from_client, model)
        start_time = time.time()
        collected_output: List[str] = []
        status = "success"
        error_message: str | None = None

        cookie_header, token_index = self._get_cookie()
        headers = self._prepare_headers(cookie_header)

        async def stream_generator() -> AsyncGenerator[bytes, None]:
            nonlocal status, error_message
            response = None

            try:
                # 3. 使用转换后的消息列表准备请求体
                payload = self._prepare_payload(model, smithery_formatted_messages)

                logger.info("===================== [REQUEST TO SMITHERY (Stateless)] =====================")
                logger.info(f"URL: POST {settings.CHAT_API_URL}")
                logger.info(f"PAYLOAD:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
                logger.info("=====================================================================================")

                # 使用 cloudscraper 发送请求
                response = self.scraper.post(
                    settings.CHAT_API_URL,
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=settings.API_REQUEST_TIMEOUT
                )

                if response.status_code != 200:
                    logger.error("==================== [RESPONSE FROM SMITHERY (ERROR)] ===================")
                    logger.error(f"STATUS CODE: {response.status_code}")
                    logger.error(f"RESPONSE BODY:\n{response.text}")
                    logger.error("=================================================================")

                response.raise_for_status()

                # 4. 流式处理返回的数据，确保按到达顺序即时推送给客户端
                for raw_line in response.iter_lines(chunk_size=1):
                    if not raw_line:
                        # 主动让出事件循环，避免长时间占用导致缓冲
                        await asyncio.sleep(0)
                        continue

                    line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, bytes) else raw_line
                    if not line.startswith("data:"):
                        continue

                    content = line[len("data:"):].strip()
                    if content == "[DONE]":
                        break

                    try:
                        data = json.loads(content)
                    except json.JSONDecodeError:
                        logger.warning("无法解析 SSE 数据块: %s", content)
                        continue

                    if data.get("type") == "text-delta":
                        delta_payload = data.get("delta", "")
                        if isinstance(delta_payload, dict):
                            delta_content = delta_payload.get("text", "")
                        else:
                            delta_content = delta_payload

                        if isinstance(delta_content, str):
                            collected_output.append(delta_content)
                        else:
                            normalized = str(delta_content)
                            collected_output.append(normalized)
                            delta_content = normalized

                        chunk = create_chat_completion_chunk(request_id, model, delta_content)
                        yield create_sse_data(chunk)
                        await asyncio.sleep(0)

                # 5. 无状态模式下，无需保存任何会话，直接发送结束标志
                final_chunk = create_chat_completion_chunk(request_id, model, "", "stop")
                yield create_sse_data(final_chunk)
                await asyncio.sleep(0)
                yield DONE_CHUNK

            except Exception as e:
                logger.error(f"处理流时发生错误: {e}", exc_info=True)
                status = "error"
                error_message = str(e)
                safe_error_message = f"内部服务器错误: {str(e)}"
                error_chunk = create_chat_completion_chunk(request_id, model, safe_error_message, "stop")
                yield create_sse_data(error_chunk)
                yield DONE_CHUNK

            finally:
                completed_at = time.time()
                completion_text = "".join(collected_output)
                completion_tokens = estimate_tokens_for_text(completion_text, model)
                duration_ms = (completed_at - start_time) * 1000

                record = {
                    "id": request_id,
                    "model": model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                    "started_at": start_time,
                    "completed_at": completed_at,
                    "duration_ms": duration_ms,
                    "status": status,
                    "error_message": error_message,
                    "token_index": token_index,
                    "client_ip": client_ip,
                }

                try:
                    metrics_store.add_record(record)
                except Exception:
                    logger.exception("记录请求指标失败")

                if response is not None:
                    response.close()

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    def _prepare_headers(self, cookie_header: str) -> Dict[str, str]:
        # 包含我们之前分析出的所有必要请求头
        return {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
            "Cookie": cookie_header,
            "Origin": "https://smithery.ai",
            "Referer": "https://smithery.ai/playground",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "priority": "u=1, i",
            "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-posthog-distinct-id": "5905f6b4-d74f-46b4-9b4f-9dbbccb29bee",
            "x-posthog-session-id": "0199f71f-8c42-7f9a-ba3a-ff5999dd444a",
            "x-posthog-window-id": "0199f71f-8c42-7f9a-ba3a-ff5ab5b20a8e",
        }

    def _prepare_payload(self, model: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "messages": messages,
            "tools": [],
            "model": model,
            "systemPrompt": "You are a helpful assistant."
        }

    async def get_models(self) -> JSONResponse:
        model_data = {
            "object": "list",
            "data": [
                {"id": name, "object": "model", "created": int(time.time()), "owned_by": "lzA6"}
                for name in model_visibility_store.visible_models
            ]
        }
        return JSONResponse(content=model_data)
