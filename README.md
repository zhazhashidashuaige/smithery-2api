<div align="center">

# 🤖 smithery-2api 🤖

**将 [Smithery.ai](https://smithery.ai/) 强大的 AI 模型能力无缝转换为兼容 OpenAI API 格式的转换器**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python Version](https://img.shields.io/badge/python-3.10+-brightgreen.svg)](https://www.python.org/)
[![Docker Support](https://img.shields.io/badge/docker-supported-blue.svg?logo=docker)](https://www.docker.com/)
[![GitHub Repo stars](https://img.shields.io/github/stars/lzA6/smithery-2api?style=social)](https://github.com/lzA6/smithery-2api)

</div>

> "任何足够先进的技术，都与魔法无异。" —— 亚瑟·克拉克
>
> 我们不创造魔法，我们只是让每个人都能成为魔法师。`smithery-2api` 的诞生，源于一个简单的信念：强大的工具应该被更广泛、更便捷地使用。我们希望通过这个项目，打破平台的壁垒，将 Smithery.ai 先进的模型能力，注入到每一个支持 OpenAI API 的应用生态中。

---

## ✨ 核心特性

*   **🚀 零成本接入** - 免费将 Smithery.ai 的多种模型接入现有 OpenAI 生态
*   **🔌 高度兼容** - 完全模拟 OpenAI 的 `/v1/chat/completions` 和 `/v1/models` 接口
*   **🔄 多账号轮询** - 支持配置多个 Smithery.ai 账号，自动轮询提高稳定性
*   **💨 无状态设计** - 极致轻量，易于水平扩展，保护用户隐私
*   **☁️ 穿透 Cloudflare** - 内置自动处理 Cloudflare 防护机制
*   **📦 Docker 一键部署** - 一条命令即可启动服务
*   **🛰️ Zeabur 云部署** - 内置 `PORT` 适配，几分钟完成上线
*   **📊 请求监控面板** - 自带仪表盘实时查看耗时与 Token 消耗趋势
*   **🔓 开源自由** - 采用 Apache 2.0 协议，自由使用、修改和分发

---

## 🏗️ 架构设计

### 核心工作原理

`smithery-2api` 充当一个智能的协议转换器，在 OpenAI API 格式和 Smithery.ai 内部 API 格式之间进行实时转换。

```mermaid
graph TB
    Client[第三方应用] -->|OpenAI 格式请求| API[smithery-2api]
    API -->|协议转换| Translator[API 转换器]
    Translator -->|身份伪装| Identity[身份伪装模块]
    Identity -->|Smithery 格式请求| Smithery[Smithery.ai 服务]
    Smithery -->|流式响应| API
    API -->|OpenAI 格式响应| Client
    
    subgraph "转换过程详解"
        Translator --> Format[格式转换]
        Identity --> Headers[请求头模拟]
        Identity --> Cookie[Cookie 处理]
        Identity --> Cloudflare[Cloudflare 穿透]
    end
```

### 技术实现细节

#### 1. API 格式转换

**技术核心**: `app/providers/smithery_provider.py` 中的 `_convert_messages_to_smithery_format` 方法

**转换示例**:

**输入 (OpenAI 格式)**:
```json
{
  "model": "gpt-4",
  "messages": [
    {"role": "user", "content": "你好，请介绍一下自己"}
  ],
  "stream": true
}
```

**输出 (Smithery.ai 格式)**:
```json
{
  "model": "gpt-4",
  "messages": [
    {
      "role": "user", 
      "parts": [{"type": "text", "text": "你好，请介绍一下自己"}],
      "id": "msg-xxxxxxxx"
    }
  ],
  "stream": true
}
```

#### 2. 身份认证与伪装

**技术核心**: `app/core/config.py` 中的 `AuthCookie` 类

```python
class AuthCookie(BaseModel):
    """Smithery 认证 Cookie 数据结构"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    user: Dict[str, Any]
    
    @property
    def header_cookie_string(self) -> str:
        """生成请求头中的 Cookie 字符串"""
        return f"sb-access-token={self.access_token}; sb-refresh-token={self.refresh_token}"
```

#### 3. 流式响应处理

**技术核心**: `app/utils/sse_utils.py` 和流式响应处理逻辑

```python
async def handle_stream_response(response, model: str):
    """处理流式响应并转换为 OpenAI 格式"""
    async for line in response.iter_lines():
        if line.startswith('data: '):
            data = line[6:]  # 移除 'data: ' 前缀
            if data == '[DONE]':
                yield create_chat_completion_chunk("", "", finish_reason="stop")
                break
            # 解析和转换数据...
            yield f"data: {json.dumps(converted_data)}\n\n"
```

---

## 🚀 快速开始

### 环境要求

- **Git** - 版本控制工具
- **Docker & Docker Compose** - 容器化部署

### 部署步骤

#### 步骤 1: 获取项目代码

```bash
git clone https://github.com/lzA6/smithery-2api.git
cd smithery-2api
```

#### 步骤 2: 获取认证信息

1. 在浏览器中登录 [Smithery.ai](https://smithery.ai/)
2. 打开开发者工具 (F12)
3. 切换到 **Application** → **Local Storage** → `https://smithery.ai`
4. 找到 key 为 `sb-spjawbfpwezjfmicopsl-auth-token` 的项
5. 复制完整的 value 值（JSON 格式）

![获取 Cookie 演示](https://i.imgur.com/aY2i1sA.gif)

#### 步骤 3: 配置环境变量

```bash
# 复制环境配置模板
cp .env.example .env

# 编辑配置文件
vim .env
```

**环境变量配置示例**:
```env
# API 主密钥（用于客户端认证）
API_MASTER_KEY="your-secure-master-key-here"

# Smithery.ai 认证信息（支持多个账号）
SMITHERY_COOKIE_1='{"access_token":"eyJ...","token_type":"bearer","expires_in":3600,...}'
SMITHERY_COOKIE_2='{"access_token":"eyJ...","token_type":"bearer","expires_in":3600,...}'

# 服务端口配置
APP_PORT=8000

# 指标持久化（可选）
METRICS_DB_PATH="./data/metrics.sqlite"

# 模型可见性配置持久化（可选）
MODEL_VISIBILITY_PATH="./data/hidden_models.json"
```

#### 步骤 4: 启动服务

```bash
docker-compose up -d
```

#### 步骤 5: 验证部署

使用 curl 测试服务是否正常运行：

```bash
curl -X GET "http://localhost:8000/v1/models" \
  -H "Authorization: Bearer your-secure-master-key-here"
```

## 💾 在 Zeabur 上启用持久化指标存储

默认情况下，服务会将最近的请求指标保存在内存中。要在 Zeabur 上持久化这些数据，只需利用平台提供的 **Persistent Storage** 功能：

1. **创建存储卷**
   - 登录 [Zeabur 控制台](https://dash.zeabur.com/)，打开已部署的 `smithery-2api` 服务。
   - 在左侧菜单中进入 **Storage**，点击 **New Storage** 按钮。
   - 选择合适的容量（例如 1 GiB），并将 **Mount Path** 设置为 `/data`，确认创建。

2. **绑定存储并重启服务**
   - 创建完成后，在同一页面中点击 **Attach** 将存储卷挂载到当前服务实例。
   - Zeabur 会自动触发一次重新部署，使容器内出现可读写的 `/data` 目录。

3. **配置环境变量**
   - 在服务的 **Environment Variables** 面板中新增：

     ```env
     METRICS_DB_PATH=/data/metrics.sqlite
     ```

   - 保存后再次部署即可。服务会在首次运行时自动创建并维护该 SQLite 数据库文件。

4. **验证持久化是否生效**
   - 访问 `/metrics/requests` 或 `/metrics/summary`，执行一两次 API 调用。
   - 在 Zeabur 控制台中点击 **Restart** 重启服务，刷新仪表盘应仍能看到之前的调用记录。

> ℹ️ 提示：如果希望在本地或 Docker 环境中测试持久化效果，可以提前创建目录并设置同样的环境变量，例如：
>
> ```bash
> mkdir -p data
> export METRICS_DB_PATH="$(pwd)/data/metrics.sqlite"
> ```
>
> 之后启动应用即可在 `data/metrics.sqlite` 中查看到同样的指标数据。

> ⚠️ 注意：SQLite 更适合单实例部署。如果需要在 Zeabur 上水平扩展到多实例，请为每个实例绑定独立的持久化卷，或改用专用数据库服务（如 PostgreSQL）。

## 📊 请求监控面板

项目自带一个零部署成本的 Web 仪表盘，可用于观察每一次请求的耗时、模型、Prompt/Completion Tokens 以及指定时间范围内的 Token 总消耗。

1. 打开浏览器访问 `http://<你的服务域名>/dashboard`
2. 在页面顶部填入用于访问 `/metrics/*` 的 Bearer Token（即 `.env` 中的 `API_MASTER_KEY`），点击 “保存密钥”
3. 通过时间范围选择器（`datetime-local`）限定统计窗口，或直接使用默认的最近记录
4. 点击 “刷新数据” 即可拉取实时指标；也可开启自动刷新功能，每 30 秒同步一次

仪表盘会展示：

- **单次请求表格**：完成时间、模型名称、Prompt/Completion/Total Token 数、耗时以及状态（失败请求会以红色醒目显示）
- **汇总卡片**：统计时间窗口内的总请求数、成功率、输入输出 Token 累计值以及平均响应耗时

> ⚠️ 指标存储位于内存，仅用于快速观测。如果需要长期留存或跨实例聚合，可基于 `/metrics/requests` 与 `/metrics/summary` 接口对接外部监控系统。

### 模型可见性管理

仪表盘新增了“模型可见性”管理区域，用于动态屏蔽不希望对外暴露的模型：

1. 访问 `/dashboard`，在顶部输入 API 主密钥后点击“保存密钥”。
2. 在“模型可见性”卡片中勾选需要屏蔽的模型并点击“保存模型设置”。
3. 保存成功后，所选模型会立即从 `/v1/models` 响应中隐藏，且用户调用被屏蔽的模型时会返回 `403`，错误信息为 **“该模型暂时被屏蔽”**。

> ℹ️ 服务会自动把最新配置写入 `MODEL_VISIBILITY_PATH` 指定的 JSON 文件，实现持久化。也可以直接通过接口管理：
>
> - `GET /settings/models/visibility`：查看所有模型及当前屏蔽状态
> - `PUT /settings/models/visibility`：更新 `hidden_models` 数组（需携带 Bearer Token）

### 客户端配置示例

**OpenAI 官方客户端**:
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-secure-master-key-here"
)

response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello, world!"}],
    stream=True
)
```

**第三方应用配置**:
- **Base URL**: `http://localhost:8000/v1`
- **API Key**: `your-secure-master-key-here`
- **Model**: 任意支持的模型名称

---

## ☁️ Zeabur 部署指南

1. **准备代码仓库**：将本项目 Fork/推送到自己的 GitHub 或 GitLab 仓库，确保 `Dockerfile` 和 `requirements.txt` 位于仓库根目录。
2. **创建服务**：在 [Zeabur 控制台](https://dashboard.zeabur.com/) 新建项目，选择「Add Service」→「Git Repository」，关联上一步的仓库与分支。
3. **构建配置**：保持默认的 Docker 构建流程，Zeabur 会自动识别项目中的 `Dockerfile`。无需额外的 Nginx 或反向代理配置。
4. **启动命令**：在「Deploy Config」中确认启动命令为 `uvicorn main:app --host 0.0.0.0 --port $PORT`。平台会自动注入 `PORT` 环境变量，本项目已适配该端口。
5. **环境变量**：在「Environment Variables」面板中新增：
   - `API_MASTER_KEY`：用于客户端认证的主密钥
   - `SMITHERY_COOKIE_1`：从浏览器复制的 Smithery 认证 JSON
   - `SMITHERY_COOKIE_2`...（可选）：用于多账号轮询
   - `SESSION_CACHE_TTL`（可选）：会话缓存时间，默认 3600 秒
6. **部署与验证**：点击「Deploy」。当日志提示 `Application startup complete.` 后，即可通过分配的域名访问接口。
7. **连通性测试**：

```bash
curl -X GET "https://<your-service>.zeabur.app/v1/models" \
  -H "Authorization: Bearer ${API_MASTER_KEY}"
```

> ✅ 提示：Zeabur 默认开启 HTTPS，且不进行代理缓存。本项目在服务端关闭了 X-Accel 缓冲并优化了 SSE 推送，可确保聊天响应实时输出。

---

## 🛠️ 技术架构

### 技术栈

| 技术组件 | 版本 | 用途 | 关键特性 |
|---------|------|------|----------|
| **FastAPI** | 0.104+ | Web 框架 | 异步支持，自动文档生成 |
| **Pydantic** | 2.5+ | 数据验证 | 类型提示，配置管理 |
| **Cloudscraper** | 1.2+ | 反爬虫绕过 | Cloudflare 穿透 |
| **Uvicorn** | 0.24+ | ASGI 服务器 | 高性能异步服务器 |
| **Docker** | 20.10+ | 容器化 | 环境隔离，一键部署 |

### 项目结构

```
smithery-2api/
├── 📁 app/                          # 应用核心代码
│   ├── 📁 core/                     # 核心模块
│   │   ├── __init__.py
│   │   └── config.py               # 配置管理，环境变量处理
│   ├── 📁 providers/               # 服务提供商模块
│   │   ├── __init__.py
│   │   ├── base_provider.py        # 提供商基类
│   │   └── smithery_provider.py    # Smithery.ai 提供商实现
│   ├── 📁 services/                # 业务服务层
│   │   ├── session_manager.py      # 会话管理（预留）
│   │   └── tool_caller.py          # 工具调用（预留）
│   └── 📁 utils/                   # 工具函数
│       └── sse_utils.py           # Server-Sent Events 工具
├── 📄 main.py                      # FastAPI 应用入口
├── 📄 Dockerfile                   # 应用镜像构建配置
├── 📄 docker-compose.yml           # 服务编排配置
├── 📄 requirements.txt             # Python 依赖
└── 📄 .env.example                 # 环境变量模板
```

### 核心模块详解

#### 1. 配置管理 (`app/core/config.py`)

```python
class Settings(BaseSettings):
    """应用配置类"""
    API_MASTER_KEY: Optional[str] = None
    AUTH_COOKIES: List[AuthCookie] = []
    API_REQUEST_TIMEOUT: int = 180
    SESSION_CACHE_TTL: int = 3600
    DEFAULT_SERVICE_PORT: int = 8000

    def __init__(self, **values):
        super().__init__(**values)
        i = 1
        while True:
            cookie_str = os.getenv(f"SMITHERY_COOKIE_{i}")
            if cookie_str:
                try:
                    self.AUTH_COOKIES.append(AuthCookie(cookie_str))
                except ValueError as e:
                    logger.warning(f"无法加载或解析 SMITHERY_COOKIE_{i}: {e}")
                i += 1
            else:
                break

        if not self.AUTH_COOKIES:
            raise ValueError("必须在 .env 文件中至少配置一个有效的 SMITHERY_COOKIE_1")

    @property
    def runtime_port(self) -> int:
        """对外暴露的运行端口，兼容 Zeabur 的 PORT 环境变量"""
        raw_port = os.getenv("PORT")
        if not raw_port:
            return self.DEFAULT_SERVICE_PORT
        try:
            return int(raw_port)
        except ValueError:
            logger.warning("PORT 环境变量不是有效的整数，已回退到默认端口 %s", self.DEFAULT_SERVICE_PORT)
            return self.DEFAULT_SERVICE_PORT

settings = Settings()
```

#### 2. Smithery 提供商 (`app/providers/smithery_provider.py`)

```python
class SmitheryProvider(BaseProvider):
    """Smithery.ai 服务提供商"""
    
    async def chat_completion(self, request: ChatCompletionRequest) -> StreamingResponse:
        """处理聊天补全请求"""
        
        # 1. 轮询获取可用 Cookie
        auth_cookie = self._get_cookie()
        
        # 2. 准备请求头和负载
        headers = self._prepare_headers(auth_cookie)
        payload = self._prepare_payload(request)
        
        # 3. 发送请求到 Smithery.ai
        response = self._make_request(headers, payload)
        
        # 4. 处理流式响应
        return self._handle_stream_response(response, request.model)

    def _convert_messages_to_smithery_format(self, messages: List[Dict]) -> List[Dict]:
        """将 OpenAI 消息格式转换为 Smithery 格式"""
        converted_messages = []
        for msg in messages:
            converted_msg = {
                "role": msg["role"],
                "parts": [{"type": "text", "text": msg["content"]}],
                "id": f"msg-{str(uuid.uuid4())[:8]}"
            }
            converted_messages.append(converted_msg)
        return converted_messages
```

---

## 🔧 高级配置

### 多账号负载均衡

支持配置多个 Smithery.ai 账号实现自动轮询：

```env
# 配置多个账号提高可用性
SMITHERY_COOKIE_1='{"access_token":"token1","refresh_token":"refresh1",...}'
SMITHERY_COOKIE_2='{"access_token":"token2","refresh_token":"refresh2",...}'
SMITHERY_COOKIE_3='{"access_token":"token3","refresh_token":"refresh3",...}'
```

### 自定义模型映射

在 `smithery_provider.py` 中配置模型映射关系：

```python
MODEL_MAPPING = {
    "gpt-4": "gpt-4",
    "gpt-3.5-turbo": "claude-haiku-4.5", 
    "claude-3-opus": "claude-opus-3.0"
}
```

---

## 🐛 故障排除

### 常见问题

**1. 认证失败**
```
错误信息: Authentication failed or cookie expired
解决方案: 重新获取 SMITHERY_COOKIE 值
```

**2. Cloudflare 拦截**
```
错误信息: 403 Forbidden or Cloudflare challenge
解决方案: 确保 cloudscraper 版本最新，或更新请求头信息
```

**3. 流式响应中断**
```
错误信息: 连接提前关闭
解决方案: 检查客户端超时设置，确保网络稳定性
```

### 日志调试

启用详细日志输出：

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### 健康检查

```bash
# 检查服务状态
curl -X GET "http://localhost:8000/health"

# 检查模型列表
curl -X GET "http://localhost:8000/v1/models" \
  -H "Authorization: Bearer your-api-key"
```

---

## 🆕 最近更新

> **2025-10-22**

- ✨ 修复 SSE 数据块缓冲导致的“伪流式”问题，现在回复将实时推送。
- 🧹 精简 Docker Compose，移除多余的 Nginx 转发层，默认直接暴露 FastAPI 服务。
- ☁️ 新增 Zeabur 部署流程说明，并默认兼容平台注入的 `PORT` 变量。

---

## 🚧 限制与待完善功能

### 当前限制

1. **令牌过期处理** - 当前未实现自动刷新机制
2. **错误处理** - 错误信息返回可以更加友好
3. **速率限制** - 缺少请求频率限制
4. **会话管理** - 无状态设计，客户端需维护完整上下文

### 开发路线图

- [ ] **自动令牌刷新机制**
  - 利用 refresh_token 自动更新 access_token
  - 实现令牌过期前预刷新

- [ ] **增强的错误处理**
  - 更友好的错误消息
  - 重试机制和故障转移

- [ ] **会话状态管理**
  - 可选的有状态会话模式
  - Redis 后端支持

- [ ] **监控和指标**
  - Prometheus 指标收集
  - 请求统计和性能监控

- [ ] **扩展提供商支持**
  - 支持其他 AI 服务平台
  - 统一的提供商接口

---

## 🤝 贡献指南

我们欢迎各种形式的贡献！

### 报告问题

如果您发现任何问题，请通过 [GitHub Issues](https://github.com/lzA6/smithery-2api/issues) 报告。

### 代码贡献

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

### 开发环境设置

```bash
# 克隆仓库
git clone https://github.com/lzA6/smithery-2api.git
cd smithery-2api

# 安装依赖
pip install -r requirements.txt

# 设置环境变量
cp .env.example .env
# 编辑 .env 文件配置认证信息

# 启动开发服务器
uvicorn main:app --reload --port 8000
```

---

## 📄 许可证

本项目采用 Apache 2.0 许可证 - 详见 [LICENSE](LICENSE) 文件。

## 🙏 致谢

感谢所有为这个项目做出贡献的开发者，以及 Smithery.ai 提供的优秀 AI 服务。

---

## 🔗 有用链接

- [Smithery.ai 官网](https://smithery.ai/)
- [OpenAI API 文档](https://platform.openai.com/docs/api-reference)
- [FastAPI 文档](https://fastapi.tiangolo.com/)
- [Docker 文档](https://docs.docker.com/)

---

<div align="center">

**如果这个项目对您有帮助，请给个 ⭐️ 支持一下！**

</div>
