# Day 14 Lab — Mission Answers

> Môi trường chạy: Windows 11 + Docker Desktop 29.8 (WSL2). Các bài cần Linux (SIGTERM, benchmark Part 7) chạy trong Ubuntu (WSL2) hoặc container `python:3.11-slim`.
> Mọi output bên dưới là output thật từ lệnh đã chạy; token/key đã được che.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng | Hậu quả |
|---|---|---|---|
| 1 | Hardcode secret: `OPENAI_API_KEY = "sk-hardcoded-..."`, `DATABASE_URL` chứa `admin:password123` | 17–18 | Push lên GitHub là lộ key/mật khẩu; đổi key phải sửa code và deploy lại |
| 2 | **Log ra secret**: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 34 | Key nằm trong log của platform, ai đọc được log là lấy được key |
| 3 | Dùng `print()` thay cho logging, không có level, không có cấu trúc | 33, 38 | Không lọc/tìm kiếm được trên log aggregator, không tắt được log debug |
| 4 | Config cố định trong code: `DEBUG = True`, `MAX_TOKENS = 500` | 21–22 | Không đổi được giữa dev/staging/prod nếu không sửa code |
| 5 | Không có `/health` (đã gọi thử: `GET /health` → **404**) | 42–43 | Platform không biết app chết để restart, load balancer không biết để bỏ instance |
| 6 | `host="localhost"` | 51 | Trong container chỉ nghe loopback → từ ngoài container không gọi được |
| 7 | `port=8000` cố định, không đọc `PORT` | 52 | Railway/Render cấp port qua biến `PORT` → app nghe sai port, deploy fail health check |
| 8 | `reload=True` | 53 | Chế độ dev: tốn tài nguyên theo dõi file, không dùng cho production |
| 9 | Không xử lý shutdown (không lifespan, không SIGTERM) | — | Khi platform tắt/redeploy, request đang chạy bị cắt ngang |
| 10 | `question` nhận qua query string, không validate độ dài | 31 | Câu hỏi nằm trong URL (bị ghi vào access log), không giới hạn input |

### Exercise 1.2: Chạy bản develop

```text
$ curl -X POST "http://localhost:8000/ask?question=Hello"
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP 422          ← bản develop chỉ nhận question qua query

$ curl http://localhost:8000/health
{"detail":"Not Found"} (HTTP 404)
```

Log server — secret bị in ra màn hình:

```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.
```

Nó chạy, nhưng chưa production-ready: lộ secret trong log, không có health check, chỉ nghe `localhost`.

### Exercise 1.3: So sánh develop vs production

Bản production chạy thật (`PORT=8000 python app.py` trong `production/`):

```text
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.8,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T09:06:44.742979+00:00"}
$ curl http://localhost:8000/ready
{"ready":true}
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","model":"gpt-4o-mini"}
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`DEBUG=True`, key, DB URL) | `config.py` đọc env (`os.getenv`), có `validate()` fail-fast khi thiếu `AGENT_API_KEY` ở production | Cùng 1 image chạy được ở mọi môi trường, đổi config không cần sửa code; secret không nằm trong git |
| Secret | `sk-...` trong code và in ra log | Đọc từ env, **không** log secret (chỉ log `question_length`) | Lộ secret = mất tiền/mất dữ liệu; log thường được nhiều người/hệ thống đọc |
| Health check | Không có (404) | `/health` (liveness) + `/ready` (readiness, 503 khi chưa sẵn sàng) | Platform dùng `/health` để restart container chết, LB dùng `/ready` để không gửi traffic vào instance chưa sẵn sàng |
| Logging | `print()` | Log JSON qua `logging` | Log JSON parse được → lọc theo field, đếm lỗi, nối với trace |
| Shutdown | Đột ngột | `lifespan` (startup/shutdown) + handler SIGTERM | Redeploy không cắt ngang request đang chạy, đóng kết nối sạch |
| Host/Port | `localhost:8000` cố định | `0.0.0.0` + `PORT` từ env | Chạy được trong container và trên cloud (platform cấp `PORT`) |
| Reload | `reload=True` luôn bật | Chỉ khi `DEBUG=true` | Production không cần theo dõi file, tiết kiệm tài nguyên |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi được từ trình duyệt |

**Quan sát thêm khi chạy:** log của bản production **không** ra JSON như mong đợi. Log in ra là `WARNING:root:OPENAI_API_KEY not set — using mock LLM`, còn các dòng `logger.info(json.dumps(...))` không hiện. Nguyên nhân: `config.py` gọi `logging.warning()` lúc import (trước `logging.basicConfig` trong `app.py`), nên root logger đã tự cấu hình format mặc định + level WARNING; `basicConfig` sau đó không có tác dụng. Đây là lý do ở Part 6 em tự gắn handler + `JsonFormatter` riêng cho logger của app thay vì dựa vào `basicConfig`.

---

## Part 2: Docker

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` — bản Debian đầy đủ có sẵn Python 3.11 và build tools (gcc, headers…), nên rất nặng.
2. **Working directory:** `/app` (`WORKDIR /app`) — mọi lệnh `COPY`/`RUN`/`CMD` sau đó chạy trong thư mục này.
3. **Vì sao COPY `requirements.txt` trước:** Docker cache theo từng layer. `requirements.txt` ít thay đổi → layer `pip install` được dùng lại từ cache; chỉ khi đổi dependencies mới cài lại. Nếu `COPY . .` trước thì sửa 1 dòng code cũng làm mất cache và phải `pip install` lại từ đầu.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh/tham số mặc định, bị thay thế khi chạy `docker run image <lệnh khác>`. `ENTRYPOINT` là chương trình cố định luôn chạy; tham số của `docker run` được nối vào sau nó (chỉ đổi được bằng `--entrypoint`). Hay dùng kết hợp: `ENTRYPOINT ["uvicorn"]` + `CMD ["app:app", "--port", "8000"]`.

### Exercise 2.2: Build và chạy container develop

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -p 8000:8000 my-agent:develop
$ curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.4,"container":true}
```

### Exercise 2.3: Multi-stage build và kích thước image

- **Stage 1 (builder)** — `python:3.11-slim` + cài `gcc`, `libpq-dev`, rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này chỉ để build, không deploy.
- **Stage 2 (runtime)** — `python:3.11-slim` sạch, chỉ `COPY --from=builder /root/.local` (package đã cài) + code; tạo user `appuser` non-root, có `HEALTHCHECK`, chạy uvicorn 2 worker.
- **Vì sao nhỏ hơn:** image cuối không chứa compiler, header, apt cache, pip cache của stage builder, và dùng base `slim` thay cho base đầy đủ.

Kích thước đo bằng `docker images`:

```text
$ docker images my-agent --format "table {{.Repository}}:{{.Tag}}\t{{.Size}}"
REPOSITORY:TAG      SIZE
my-agent:advanced   240MB
my-agent:develop    1.67GB
```

- Develop: **1.67 GB** (1 667 775 839 byte)
- Production: **240 MB** (239 815 513 byte)
- Chênh lệch: **giảm 85.6%** (`(1667775839 − 239815513) / 1667775839`)

### Exercise 2.4: Docker Compose stack (`02-docker/production`)

```text
                     ┌──────────────────────── network "internal" (bridge) ──────────────────────┐
 Client ──:80/:443──▶│ nginx (reverse proxy, rate limit 10r/s, security headers)                 │
                     │   │ proxy_pass http://agent_backend (agent:8000)                           │
                     │   ▼                                                                        │
                     │ agent (FastAPI, uvicorn 2 workers, không expose port ra ngoài)             │
                     │   ├──▶ redis:6379   (cache, session, rate limit)                          │
                     │   └──▶ qdrant:6333  (vector DB cho RAG)                                   │
                     └────────────────────────────────────────────────────────────────────────────┘
```

- 4 service được start: `nginx`, `agent`, `redis`, `qdrant`. Agent chỉ start khi `redis` và `qdrant` **healthy** (`depends_on: condition: service_healthy`).
- Giao tiếp qua network nội bộ `internal`, gọi nhau bằng **tên service** (Docker DNS): `redis://redis:6379/0`, `http://qdrant:6333`. Chỉ Nginx publish port ra host.

```text
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 12 seconds (healthy)   8000/tcp
nginx     nginx:alpine            Up 11 seconds             0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 22 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 22 seconds (healthy)   6379/tcp

$ curl http://localhost/health
{"status":"ok","uptime_seconds":10.2,"version":"2.0.0","timestamp":"2026-11-25T09:10:21.873721"} (HTTP 200)
$ curl -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."} (HTTP 200)
```

---

## Part 3: Cloud Deployment

### Exercise 3.1: Railway (deploy agent Part 6 trong `06-lab-complete/`)

- URL: **https://k5-l3a-day14-hoanghailong-2a202603670-deployment-production.up.railway.app**
- Deploy: service của repo trong project Railway `renewed-determination`, thêm service Redis, biến môi trường đặt bằng `railway variable set` (`AGENT_API_KEY` qua `--stdin`), `railway up` thư mục `06-lab-complete/` (Dockerfile + `railway.toml`), `railway domain`.
- Kết quả 4 lệnh tự kiểm CP6: `/health` **200**, `/ready` **200**, `/ask` không key **401**, `/ask` có key **200**; vượt 10 request/phút → **429**.
- Sự cố khi deploy: `railway up` chạy trực tiếp trên ổ Windows (`/mnt/d`, WSL) gửi lên file toàn byte `\x00` (build báo `exclude-patterns contains non-printable ASCII`, rồi `Invalid requirement: '\x00\x00…'`). Chép `06-lab-complete/` sang filesystem Linux (`~/k5-deploy`) rồi `railway up ~/k5-deploy --path-as-root` thì build và healthcheck thành công.
- Chi tiết lệnh test, biến môi trường, ảnh chụp: [DEPLOYMENT.md](DEPLOYMENT.md)

### Exercise 3.2: So sánh `railway.toml` và `render.yaml`

| Khía cạnh | `railway.toml` | `render.yaml` |
|---|---|---|
| Định dạng / phạm vi | TOML, cấu hình **1 service** (build + deploy) | YAML Blueprint, mô tả **cả hạ tầng**: nhiều service (web + Redis/Key Value…) |
| Build | `builder = "DOCKERFILE"` (hoặc `NIXPACKS`/`RAILPACK` tự đoán) | `runtime: docker` + `dockerfilePath`, hoặc `runtime: python` + `buildCommand` |
| Start | Theo `CMD` trong Dockerfile hoặc `startCommand` | Theo `CMD` hoặc `startCommand` |
| Health check | `healthcheckPath`, `healthcheckTimeout` | `healthCheckPath` |
| Restart | `restartPolicyType`, `restartPolicyMaxRetries` | Render tự quản lý |
| Biến môi trường / secret | **Không** khai báo trong file: đặt qua dashboard hoặc `railway variables` | Khai báo ngay trong file: `value` cho config thường, `generateValue: true` (Render tự sinh) hoặc `sync: false` (nhập tay trên dashboard) cho secret |
| Redis | Tạo service Redis riêng trên dashboard rồi nối `REDIS_URL` | Khai báo service `keyvalue` trong cùng file, nối bằng `fromService … property: connectionString` |
| Vị trí file | Thư mục được `railway up` (ở đây là `06-lab-complete/`) | Render đọc ở **gốc repo**; agent nằm trong thư mục con nên phải đặt `rootDir: 06-lab-complete` |

**Vì sao khác:** Railway thiên về "deploy 1 service từ CLI, phần còn lại click trên dashboard" nên file chỉ chứa build/deploy. Render theo hướng Infrastructure-as-Code: push repo → Render đọc Blueprint và tạo toàn bộ hạ tầng, nên file phải mô tả cả service phụ trợ và cách cấp secret.

### Exercise 3.3: Pipeline Cloud Run (`03-cloud-deployment/production-cloud-run/`)

`cloudbuild.yaml` chạy 4 bước nối tiếp nhau (`waitFor`), bước trước fail thì dừng:

1. **test** — trong `python:3.11-slim`: `pip install -r requirements.txt pytest` rồi `pytest tests/`.
2. **build** — `docker build` image, gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tận dụng layer cache.
3. **push** — đẩy cả 2 tag lên Container Registry.
4. **deploy** — `gcloud run deploy ai-agent` với image theo `$COMMIT_SHA` (biết chính xác commit nào đang chạy, rollback dễ), region `asia-southeast1`, min 1 / max 10 instance, 512Mi, `ENVIRONMENT=production`, secret `OPENAI_API_KEY` lấy từ **Secret Manager**.

`service.yaml` là bản khai báo (IaC) của chính service đó: autoscaling 1–10 instance, mỗi instance tối đa 80 request đồng thời, timeout 60s, secret qua `secretKeyRef`, `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready`.

---

## Part 4: API Security

### Exercise 4.1: API key (`04-api-gateway/develop`, chạy `AGENT_API_KEY=secret-key-123 python app.py`)

- **Key được kiểm ở đâu:** hàm `verify_api_key` (dòng 39–54) là FastAPI dependency, đọc header `X-API-Key` qua `APIKeyHeader(auto_error=False)`; endpoint `/ask` gắn `_key: str = Depends(verify_api_key)`. `/` và `/health` không gắn → public.
- **Sai key:** thiếu header → **401**; có header nhưng sai → **403**. (Part 6 yêu cầu cả hai là 401 nên bản của em trả 401 cho cả hai trường hợp.)
- **Rotate key:** key đọc từ env `AGENT_API_KEY` → đặt giá trị mới trên platform rồi restart/redeploy, không sửa code. Muốn không downtime thì cho app chấp nhận đồng thời key cũ và key mới (vd `AGENT_API_KEYS=old,new`), báo client chuyển sang key mới, sau đó gỡ key cũ.

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2: JWT (`04-api-gateway/production`)

**JWT flow (`auth.py`):** `POST /auth/token` kiểm username/password → `create_token()` tạo payload `{sub, role, iat, exp = now + 60 phút}` và **ký** HS256 bằng `JWT_SECRET`. Client gửi `Authorization: Bearer <token>`. `verify_token()` kiểm chữ ký + hạn dùng bằng `jwt.decode` → lấy `username`, `role` ngay trong token, không cần tra DB. Thiếu token → 401, token hết hạn → 401, token sai chữ ký → 403.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username": "student", "password": "demo123"}'
HTTP/1.1 200 OK
{"access_token":"eyJ***(đã che)***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 401 Unauthorized
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic.","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

### Exercise 4.3: Rate limiting

- **Thuật toán:** *sliding window log* — mỗi user có 1 `deque` timestamp; mỗi request xoá timestamp cũ hơn 60s rồi đếm số còn lại.
- **Giới hạn:** `rate_limiter_user` 10 request/60s, `rate_limiter_admin` 100 request/60s.
- **Bypass cho admin:** `app.py` chọn limiter theo `role` trong JWT: `admin` dùng limiter 100/phút. Muốn bypass hẳn thì bỏ qua `limiter.check()` khi `role == "admin"`.
- **Hạn chế:** state nằm trong RAM của process → mỗi worker/instance đếm riêng, restart là mất (Part 6 đã chuyển sang Redis).

Vòng lặp 20 request với token `student` (request "có token" ở 4.2 đã dùng 1 lượt, nên request thứ 10 của vòng lặp là lượt thứ 11 trong cửa sổ 60s):

```text
$ for i in {1..20}; do curl -s -o /dev/null -w "Request $i: %{http_code}\n" -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Test '$i'"}'; done
Request 1: 200
Request 2: 200
Request 3: 200
Request 4: 200
Request 5: 200
Request 6: 200
Request 7: 200
Request 8: 200
Request 9: 200
Request 10: 429
Request 11: 429
...
Request 20: 429

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" ...
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795597933
retry-after: 53
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":53}}
```

### Exercise 4.4: Cost guard

**Bản mẫu (`04-api-gateway/production/cost_guard.py`):** đếm token theo user theo ngày trong RAM, `check_budget()` gọi **trước** LLM (user vượt $1/ngày → 402, toàn hệ thống vượt $10/ngày → 503), `record_usage()` gọi **sau** LLM để cộng token. Khi chạy:

```text
$ curl http://localhost:8000/me/usage -H "Authorization: Bearer $TOKEN"
{"user_id":"student","date":"2026-11-25","requests":10,"input_tokens":40,"output_tokens":296,"cost_usd":0.000184,"budget_usd":1.0,"budget_remaining_usd":0.999816,"budget_used_pct":0.0}
```

(Lỗi nhỏ của bản mẫu: field `usage.budget_remaining_usd` trong response `/ask` thực ra trả `total_cost_usd` — số đã tiêu, không phải số còn lại.)

**Cách em làm (Part 6, `06-lab-complete/app/cost_guard.py`) — state trong Redis để mọi instance dùng chung:**

1. Mock LLM không trả token → ước lượng `tokens ≈ len(text) / 4`; giá tham khảo gpt-4o-mini: input $0.15/1M, output $0.60/1M token.
2. 2 key Redis theo thời gian: `cost:daily:YYYY-MM-DD` (TTL 2 ngày) và `cost:monthly:YYYY-MM` (TTL 32 ngày) → **tự reset** sang ngày/tháng mới vì key mới, không cần cron.
3. `check_budget()` gọi **trước** LLM: `MGET` 2 key, nếu `daily ≥ DAILY_BUDGET_USD` hoặc `monthly ≥ MONTHLY_BUDGET_USD` → **402 Payment Required** (không tốn tiền gọi LLM nữa).
4. `record_cost()` gọi **sau** LLM: `INCRBYFLOAT` cả 2 key trong 1 pipeline (atomic, nhiều worker cộng cùng lúc không mất số).
5. Redis lỗi lúc check → 503 (fail-closed: không chắc còn budget thì không gọi LLM); lỗi lúc record → chỉ ghi log vì câu trả lời đã có.

Hàm theo đề bài Exercise 4.4 (budget $10/tháng **mỗi user**):

```python
def check_budget(user_id: str, estimated_cost: float) -> bool:
    key = f"budget:{user_id}:{datetime.now(timezone.utc):%Y-%m}"   # key theo tháng → tự reset đầu tháng
    # INCRBYFLOAT trước rồi mới kiểm → 2 request song song không cùng "lọt" qua ngưỡng
    new_total = r.incrbyfloat(key, estimated_cost)
    r.expire(key, 32 * 24 * 3600)
    if new_total > 10:
        r.incrbyfloat(key, -estimated_cost)   # hoàn lại phần vừa cộng
        return False
    return True
```

Rate limit và cost guard bảo vệ 2 thứ khác nhau: rate limit chặn **tốc độ** (1 client spam làm sập service), cost guard chặn **tổng tiền** (nhiều client hợp lệ, mỗi người gọi chậm, nhưng cộng lại vẫn vượt ngân sách). Cần cả hai.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health check vs readiness (`05-scaling-reliability/develop`)

- `/health` (**liveness**): process còn sống không → luôn 200 nếu event loop còn chạy (kèm uptime, % RAM). Fail → platform **restart** container.
- `/ready` (**readiness**): có nên nhận traffic chưa → 503 khi đang khởi động, đang shutdown, hoặc dependency (Redis/DB) chưa kết nối. Fail → LB **ngừng gửi traffic**, không restart.
- Không gộp 2 endpoint: nếu `/health` kiểm cả Redis thì Redis chết 1 phút → platform restart **mọi** instance vô ích (restart không sửa được Redis). Vì vậy ở Part 6 `/health` luôn 200, còn `/ready` trả 503 khi mất Redis.

```text
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.4,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T09:47:37.228056+00:00","checks":{"memory":{"status":"ok","used_percent":26.4}}}
$ curl http://localhost:8000/ready
{"ready":true,"in_flight_requests":1}
```

### Exercise 5.2: Graceful shutdown (chạy trên Linux)

```text
$ python app.py &          # PID 365
$ curl -X POST "http://localhost:8000/ask?question=Long%20task" &
$ kill -TERM 365
request đang chạy nhận được: {"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."} (HTTP 200)
process exit code: 0
```

Log server:

```text
INFO:     Shutting down
INFO:     127.0.0.1:37572 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK   ← request đang chạy vẫn hoàn thành
INFO:     Waiting for application shutdown.
2026-11-25 09:47:40,392 INFO 🔄 Graceful shutdown initiated...
2026-11-25 09:47:40,392 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [365]
2026-11-25 09:47:40,392 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

Thứ tự: uvicorn nhận SIGTERM → ngừng nhận kết nối mới → chờ request đang chạy xong (vẫn 200) → chạy phần shutdown của `lifespan` → thoát exit code 0. Dòng `Received signal 15` của handler tự viết in ra **cuối cùng**: `uvicorn.run()` thay handler SIGTERM của app bằng handler của nó lúc khởi động, khi tắt xong mới trả lại và gọi handler cũ. Nếu tự viết handler mà không gọi lại handler của uvicorn thì process sẽ không bao giờ tắt (đúng như cảnh báo trong CODE_LAB Step 7).

### Exercise 5.3: Stateless design

`05-scaling-reliability/production/app.py` lưu session/history trong Redis (`session:<id>`, TTL 1 giờ, giữ tối đa 20 message) thay vì dict trong RAM. Lý do: khi chạy nhiều instance sau load balancer, request 1 của user có thể vào instance A, request 2 vào instance B — nếu history nằm trong RAM của A thì B không thấy, và instance bị restart/scale-down là mất hết. Đưa state ra Redis thì instance nào cũng phục vụ được bất kỳ user nào. Part 6 áp dụng cùng nguyên tắc cho rate limit và cost guard.

### Exercise 5.4: Load balancing (`docker compose -p k5-day14-scaling up -d --build --scale agent=3`)

```text
NAME                       STATUS                            PORTS
k5-day14-scaling-agent-1   Up 4 seconds (health: starting)   8000/tcp
k5-day14-scaling-agent-2   Up 4 seconds (health: starting)   8000/tcp
k5-day14-scaling-agent-3   Up 4 seconds (health: starting)   8000/tcp
k5-day14-scaling-nginx-1   Up 3 seconds                      0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 15 seconds (healthy)           6379/tcp
```

10 request qua Nginx (cổng 8080, `POST /chat`):

```text
Request 1 -> served_by instance-5a6588
Request 2 -> served_by instance-aee37e
Request 3 -> served_by instance-5a6588
Request 4 -> served_by instance-613bb1
Request 5 -> served_by instance-aee37e
Request 6 -> served_by instance-5a6588
Request 7 -> served_by instance-613bb1
Request 8 -> served_by instance-aee37e
Request 9 -> served_by instance-5a6588
Request 10 -> served_by instance-613bb1
```

`docker compose logs agent` — số request `/chat` mỗi instance nhận: agent-1: 3, agent-2: 5, agent-3: 3 → Nginx chia tải cho cả 3.

**Sự cố gặp phải:** gọi ngay sau `up` thì Nginx trả **502** (`no live upstreams`). Nginx khởi động trước khi các agent sẵn sàng; vài lần kết nối lỗi làm Nginx đánh dấu cả 3 upstream "down" trong `fail_timeout` (10s). Chờ agent `healthy` rồi gọi lại thì bình thường. Production nên cho Nginx `depends_on: condition: service_healthy`.

**Tắt hẳn 1 instance giữa chừng** — traffic tự chuyển sang 2 instance còn lại, history không mất:

```text
session: 9381e5c8-b479-4729-8f20-b83624f4c76b
$ docker stop k5-day14-scaling-agent-2
turn 3 -> served_by instance-613bb1
turn 4 -> served_by instance-aee37e
turn 5 -> served_by instance-613bb1
turn 6 -> served_by instance-aee37e
turn 7 -> served_by instance-613bb1
turn 8 -> served_by instance-aee37e
history count: 14
```

(7 lượt hỏi × 2 message = 14. Số `turn` bị lệch +1 vì code mẫu đếm câu hỏi **sau khi** đã ghi câu hỏi hiện tại vào history rồi còn cộng thêm 1.)

### Exercise 5.5: `python test_stateless.py`

```text
Session ID: 7e8ec8fd-ba57-4a9f-8cc7-c372d6dea532

Request 1: [instance-aee37e]   Q: What is Docker?
Request 2: [instance-5a6588]   Q: Why do we need containers?
Request 3: [instance-613bb1]   Q: What is Kubernetes?
Request 4: [instance-aee37e]   Q: How does load balancing work?
Request 5: [instance-5a6588]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-5a6588', 'instance-613bb1', 'instance-aee37e'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ...
  [user]: What is Redis used for?...
  [assistant]: Đây là câu trả lời từ AI agent (mock). Trong production, đây...

✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu trong cùng 1 session rơi vào cả 3 instance khác nhau (round-robin), nhưng history vẫn đủ 10 message (5 user + 5 assistant) theo đúng thứ tự, vì không instance nào giữ history trong RAM — tất cả đọc/ghi `session:<id>` trên cùng một Redis. Nếu dùng dict trong RAM, mỗi instance chỉ thấy phần history của request nó xử lý.

---

## Part 6: Final Project — output `check_production_ready.py`

Chạy bản đầy đủ (có Docker) trong Ubuntu 26.04 / WSL2, Python 3.14 (`os.name == "posix"`):

```text
$ cd 06-lab-complete && python check_production_ready.py
...
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Lần chạy đầu tiên trong Ubuntu báo `4/12` vì WSL Integration của Docker Desktop chưa bật cho Ubuntu: không có Docker → script không bật được Redis tạm → mọi request `/ask` trả 503 (`Rate limiter unavailable`) và `/ready` 503. Đây đúng là hành vi mong muốn khi mất Redis (fail-closed). Bật integration xong chạy lại thì đạt 12/12.

---

## AI log

| # | Hỏi / giao cho AI | Nhận được | Mình tự sửa / kiểm tra thế nào |
|---|---|---|---|
| 1 | Claude Code (Claude Opus 5.5): giải thích mục đích lab, kế hoạch, cần cài gì; vì sao cần WSL2/Ubuntu | Giải thích + danh sách công cụ; phát hiện `pydantic==2.9.0` không có wheel cho Python 3.14 | Tự cài Ubuntu, tạo `.venv` |
| 2 | Claude Code: viết code Part 6 (các TODO trong `06-lab-complete/`) | `config.py`, `log.py` (JsonFormatter), `auth.py`, `redis_client.py`, `rate_limiter.py` (sorted set + MULTI/EXEC), `cost_guard.py` (INCRBYFLOAT daily/monthly), `main.py` (lifespan, middleware log, exception handler), Dockerfile multi-stage, compose, `.dockerignore`, `railway.toml`, `render.yaml` | Chạy `check_production_ready.py` (static, runtime trong Linux, Docker); đọc lại từng file để giải thích được |
| 3 | Claude Code: chạy Part 1, 2, 4, 5 và soạn câu trả lời | Output thật của các lệnh + câu trả lời; phát hiện 502 khi Nginx khởi động trước agent, lỗi log JSON của Part 1 production, lỗi `budget_remaining_usd` | Đối chiếu từng con số với output lệnh |
| 4 | Claude Code: chạy Part 7 (load test, eval, sửa KNOWLEDGE_BASE, observability stack, chaos) | Report JSON/CSV, 2 entry KB mới (JWT, HPA), fix `PYTHONUSERBASE` trong Dockerfile Part 7, phát hiện `docker compose run` reset số replica (thêm `--no-deps`) | Chạy lại eval (23/23), kiểm tra số instance trước/sau mỗi lần Locust |
| 5 | Claude Code: deploy lên Railway (project `renewed-determination` có sẵn, gói Free không cho tạo project mới) | Thêm Redis, đặt biến môi trường, chẩn đoán lỗi build do `railway up` từ `/mnt/d` gửi file toàn byte 0, deploy từ bản sao trên filesystem Linux | Chạy 4 lệnh tự kiểm CP6 (200/200/401/200), vòng lặp rate limit ra 429, `check_production_ready.py --public-url` ✅ |
