# Deployment Information

## Public URL

https://k5-l3a-day14-hoanghailong-2a202603670-deployment-production.up.railway.app

## Platform

Railway. Deploy thư mục `06-lab-complete/` (agent Part 6) bằng Dockerfile, cấu hình trong [`06-lab-complete/railway.toml`](06-lab-complete/railway.toml):

- `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`, restart `ON_FAILURE` tối đa 3 lần
- Railway cấp biến `PORT`; `CMD` trong Dockerfile lắng nghe `${PORT:-8000}`
- Redis: service Redis riêng trong cùng project Railway, agent nối qua `REDIS_URL`

## Test Commands

Đặt biến trước khi test (key không ghi vào file):

```bash
export BASE="https://k5-l3a-day14-hoanghailong-2a202603670-deployment-production.up.railway.app"
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
# Tối đa 10 request có key / 60 giây, vượt → 429 (có header Retry-After)
```

### Kết quả 4 lệnh tự kiểm (CP6)

```text
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"
200
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"
200
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'
401
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'
200
--- body
{"status":"ok","uptime_s":45.6}
{"status":"ready","redis":true}
{"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":4,"output_tokens":19,"cost_usd":1.2e-05}}
--- rate limit: 15 request liên tiếp (2 request có key ở trên đã dùng 2 lượt trong cùng phút)
Request 1: 200
Request 2: 200
Request 3: 200
Request 4: 200
Request 5: 200
Request 6: 200
Request 7: 200
Request 8: 200
Request 9: 429
Request 10: 429
Request 11: 429
Request 12: 429
Request 13: 429
Request 14: 429
Request 15: 429

$ python check_production_ready.py --public-url "$BASE"
✅ Public URL: GET https://k5-l3a-day14-hoanghailong-2a202603670-deployment-production.up.railway.app/health → 200
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
- [Test results](screenshots/test.png): output thật của 4 lệnh tự kiểm chạy trong Ubuntu lúc 18:40 ngày 2026-11-25 (key đọc từ file, không hiện), hiển thị trong khung terminal bằng Playwright
