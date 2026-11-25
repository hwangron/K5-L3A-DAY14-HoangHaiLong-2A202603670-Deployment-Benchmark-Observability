# Deployment Information

## Public URL

⏳ `https://<điền-domain>.up.railway.app` — điền sau khi chạy `railway domain`

## Platform

Railway. Deploy thư mục `06-lab-complete/` (agent Part 6) bằng Dockerfile, cấu hình trong [`06-lab-complete/railway.toml`](06-lab-complete/railway.toml):

- `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`, restart `ON_FAILURE` tối đa 3 lần
- Railway cấp biến `PORT`; `CMD` trong Dockerfile lắng nghe `${PORT:-8000}`
- Redis: service Redis riêng trong cùng project Railway, agent nối qua `REDIS_URL`

## Test Commands

Đặt biến trước khi test (key không ghi vào file):

```bash
export BASE="https://<điền-domain>.up.railway.app"
export AGENT_API_KEY="<key đã đặt trên Railway>"
```

### Health check và readiness

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200 (Redis kết nối được)
curl -s "$BASE/health"
# {"status":"ok","uptime_s":...}
```

### Authentication

```bash
# Không có key → 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -d '{"question":"Hello"}'

# Có key → 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" \
  -d '{"question":"Hello"}'

curl -s -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" \
  -d '{"user_id":"test","question":"What is deployment?"}'
```

### Rate limiting (10 request/phút mỗi key)

```bash
for i in $(seq 1 15); do
  curl -sS -o /dev/null -w "Request $i: %{http_code}\n" -X POST "$BASE/ask" \
    -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"test"}'
done
# Request 1–10: 200, từ request 11: 429 (có header Retry-After)
```

### Kết quả 4 lệnh tự kiểm (CP6)

```text
⏳ dán output vào đây
```

## Environment Variables Set

Đặt trên Railway (Variables), **không** ghi trong code hay file commit:

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `AGENT_API_KEY` | *(secret, sinh ngẫu nhiên)* | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Reference tới service Redis trong project |
| `ENVIRONMENT` | `production` | |
| `RATE_LIMIT_PER_MINUTE` | `10` | |
| `DAILY_BUDGET_USD` | `1.0` | |
| `MONTHLY_BUDGET_USD` | `10.0` | |
| `LOG_LEVEL` | `INFO` | |
| `PORT` | *(Railway tự cấp)* | Không cần đặt |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
