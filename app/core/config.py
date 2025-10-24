import os
import json
import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional, Dict

# 获取一个日志记录器实例
logger = logging.getLogger(__name__)

def _mask_email(email: Optional[str]) -> Optional[str]:
    """Mask an email address as ``xx**xx@domain`` style output."""

    if not email or "@" not in email:
        return None

    local, _, domain = email.partition("@")
    if not local or not domain:
        return None

    prefix = (local[:2] or "xx").ljust(2, "x")
    if len(local) >= 2:
        suffix_source = local[-2:]
    elif len(local) == 1:
        suffix_source = (local[-1] + "x")
    else:
        suffix_source = "xx"

    suffix = suffix_source.ljust(2, "x")
    masked_local = f"{prefix}**{suffix}"
    return f"{masked_local}@{domain}"


class AuthCookie:
    """
    处理并生成 Smithery.ai 所需的认证 Cookie。
    它将 .env 文件中的 JSON 字符串转换为一个标准的 HTTP Cookie 头部字符串。
    """

    def __init__(self, json_string: str, name: Optional[str] = None):
        try:
            # 1. 解析从 .env 文件读取的 JSON 字符串
            data = json.loads(json_string)
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token")
            self.expires_at = data.get("expires_at", 0)
            self.user = data.get("user")
            self.email = None
            if isinstance(self.user, dict):
                self.email = self.user.get("email")
            self.masked_email = _mask_email(self.email)
            self.name = name or "SMITHERY_COOKIE"

            if not self.access_token:
                raise ValueError("Cookie JSON 中缺少 'access_token'")

            # 2. 构造将要放入 Cookie header 的值部分 (它本身也是一个 JSON)
            #    注意：这里我们只包含 Supabase auth 需要的核心字段
            cookie_value_data = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "token_type": data.get("token_type", "bearer"),
                "expires_in": data.get("expires_in", 3600),
                "expires_at": self.expires_at,
                "user": data.get("user")
            }
            
            # 3. 构造完整的 Cookie 键值对字符串
            #    Smithery.ai 使用的 Supabase project_ref 是 'spjawbfpwezjfmicopsl'
            project_ref = "spjawbfpwezjfmicopsl"
            cookie_key = f"sb-{project_ref}-auth-token"
            # 将值部分转换为紧凑的 JSON 字符串
            cookie_value = json.dumps(cookie_value_data, separators=(',', ':'))
            
            # 最终用于 HTTP Header 的字符串，格式为 "key=value"
            self.header_cookie_string = f"{cookie_key}={cookie_value}"

        except json.JSONDecodeError as e:
            raise ValueError(f"无法从提供的字符串中解析认证 JSON: {e}")
        except Exception as e:
            raise ValueError(f"初始化 AuthCookie 时出错: {e}")

    def __repr__(self):
        return f"<AuthCookie expires_at={self.expires_at}>"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding='utf-8',
        extra="ignore"
    )

    APP_NAME: str = "smithery-2api"
    APP_VERSION: str = "1.0.0"
    DESCRIPTION: str = "一个将 smithery.ai 转换为兼容 OpenAI 格式 API 的高性能代理，支持多账号、上下文和工具调用。"

    CHAT_API_URL: str = "https://smithery.ai/api/chat"
    TOKEN_REFRESH_URL: str = "https://spjawbfpwezjfmicopsl.supabase.co/auth/v1/token?grant_type=refresh_token"
    SUPABASE_API_KEY: str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNwamF3YmZwd2V6amZtaWNvcHNsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzQxNDc0MDUsImV4cCI6MjA0OTcyMzQwNX0.EBIg7_F2FZh4KZ3UNwZdBRjpp2fgHqXGJOvOSQ053MU"

    API_MASTER_KEY: Optional[str] = None
    
    AUTH_COOKIES: List[AuthCookie] = []

    API_REQUEST_TIMEOUT: int = 180
    SESSION_CACHE_TTL: int = 3600

    DEFAULT_SERVICE_PORT: int = 8000

    METRICS_DB_PATH: Optional[str] = None
    METRICS_MAX_IN_MEMORY_RECORDS: int = 5000

    KNOWN_MODELS: List[str] = [
        "claude-haiku-4.5", "claude-sonnet-4.5", "gpt-5", "gpt-5-mini",
        "gpt-5-nano", "gemini-2.5-flash-lite", "gemini-2.5-pro", "glm-4.6",
        "grok-4-fast-non-reasoning", "grok-4-fast-reasoning", "kimi-k2", "deepseek-reasoner"
    ]

    HIDDEN_MODELS: List[str] = [
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
    ]

    MODEL_VISIBILITY_PATH: Optional[str] = "./data/hidden_models.json"

    def __init__(self, **values):
        super().__init__(**values)
        # 从环境变量 SMITHERY_COOKIE_1, SMITHERY_COOKIE_2, ... 加载 cookies
        i = 1
        while True:
            cookie_str = os.getenv(f"SMITHERY_COOKIE_{i}")
            if cookie_str:
                try:
                    # 使用 AuthCookie 类来解析和处理 cookie 字符串
                    self.AUTH_COOKIES.append(AuthCookie(cookie_str, name=f"SMITHERY_COOKIE_{i}"))
                except ValueError as e:
                    logger.warning(f"无法加载或解析 SMITHERY_COOKIE_{i}: {e}")
                i += 1
            else:
                break
        
        if not self.AUTH_COOKIES:
            raise ValueError("必须在 .env 文件中至少配置一个有效的 SMITHERY_COOKIE_1")

    @property
    def runtime_port(self) -> int:
        """获取当前运行时使用的对外端口，兼容 Zeabur 等平台的 PORT 环境变量。"""
        raw_port = os.getenv("PORT")
        if not raw_port:
            return self.DEFAULT_SERVICE_PORT
        try:
            return int(raw_port)
        except ValueError:
            logger.warning("PORT 环境变量不是有效的整数，已回退到默认端口 %s", self.DEFAULT_SERVICE_PORT)
            return self.DEFAULT_SERVICE_PORT

    @property
    def visible_models(self) -> List[str]:
        try:
            from app.services.model_visibility_store import model_visibility_store

            return model_visibility_store.visible_models
        except Exception:
            hidden = set(self.HIDDEN_MODELS)
            return [name for name in self.KNOWN_MODELS if name not in hidden]

settings = Settings()
