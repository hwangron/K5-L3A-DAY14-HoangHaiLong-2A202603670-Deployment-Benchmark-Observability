# Benchmark & Observability Report — Day 14

**Họ tên:** Hoang Hai Long
**Ngày chạy:** 2026-11-25
**Môi trường:** Windows 11 + Docker Desktop 29.8 (WSL2 backend, 4 vCPU / 5.8 GB RAM cấp cho Docker).
Bước 1 (`develop/`) chạy trong container `python:3.11-slim` (agent và `load_test.py`/`run_eval.py` cùng một container Linux). Bước 2 (`production/`) chạy bằng Docker Compose, project `-p k5-day14-observability`.

> Mọi số liệu dưới đây lấy từ file đã nộp: `reports/*.json` (Bước 1) và `benchmark-results/scale_*_stats.csv` (Bước 2).

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500 ms** | Ở tải bình thường p95 đo được ~220 ms (mock LLM ~120–180 ms + retrieval ~20 ms) → 500 ms chừa gấp đôi biên cho LLM thật dao động mà người dùng chat vẫn thấy "nhanh". Đặt theo p95 chứ không theo avg (xem 2.2). Alert dùng ngưỡng lỏng hơn (p95 > 1 s trong 2 phút) để tránh báo động vì spike ngắn. |
| Error rate `/ask` | < **2 %** | Mock LLM có ~1% lỗi upstream giả lập (đo được 0.67–1.67%) → 2% là error budget đủ cho lỗi nền nhưng bắt được khi lỗi tăng gấp đôi. Alert ở 5% trong 1 phút. |
| Accuracy trên golden dataset | ≥ **100 %** (23/23) và **0 regression** | Bộ dataset nhỏ (23 câu) và cố định → mỗi câu sai là 1 lỗi kiến thức thật cần sửa; quality gate trong CI chặn merge khi thấp hơn hoặc khi có câu đang đúng bị sai đi. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2, 300 request mỗi mức)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.06 | 190.8 | 220.9 | 1692.2 | 1.67% (5×503) | ✅ PASS |
| 10 | 42.9 | 192.9 | 217.5 | 1704.3 | 1.00% (3×503) | ✅ PASS |
| 50 | 101.05 | 207.4 | 324.7 | 1704.5 | 1.67% (5×503) | ✅ PASS |
| 100 | 46.83 | 1264.9 | 5416.2 | 5745.8 | 0.67% (2×503) | ❌ FAIL (p95) |

File: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`.

**Nhận xét:** Từ 1 → 10 → 50, RPS tăng gần tuyến tính (4 → 43 → 101) trong khi p50 gần như đứng yên (~190–207 ms). Lý do: agent là async, mỗi request chủ yếu **chờ** LLM (`asyncio.sleep` mô phỏng network I/O), không tốn CPU, nên một event loop phục vụ song song nhiều request; theo định luật Little, `RPS ≈ concurrency / latency ≈ 10 / 0.22 s ≈ 45`. Tới 50 thì bắt đầu bão hoà: RPS chỉ đạt ~101 (lý thuyết ~230) và p95 tăng lên 325 ms.

Ở **100**, RPS **giảm** còn 46.8 và p95 vọt lên 5.4 s. Em kiểm tra xem nút thắt ở agent hay ở client bằng cách chạy **2 tiến trình `load_test.py` song song, mỗi cái concurrency 50** (tổng 100 request đồng thời vào cùng agent): tổng đạt ~203 RPS (102.4 + 101.1) với p95 ~375 ms. Như vậy agent chịu được 100 request đồng thời; nút thắt là **chính client load test**: một tiến trình Python với một event loop và một connection pool httpx không đẩy nổi hơn ~50 kết nối song song (CPU của client xử lý request/response thành nút cổ chai, request xếp hàng phía client nên latency đo được tăng). Bài học: khi benchmark phải kiểm tra load generator không phải là nút thắt (chạy nhiều process/máy, như Locust distributed).

### 2.2 Tail latency (agent v2, concurrency 20, 300 request)

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 191.9 | 219.4 | 232.3 |
| 0.03 (mặc định) | 214.9 | 233.3 | 1667.4 |
| 0.1 | 338.2 | 1684.5 | 1714.5 |

File: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`.

**Nhận xét:** Chỉ số nhạy nhất là **p99** rồi đến **p95**. Với 3% request chậm (+1.5 s), avg chỉ tăng 23 ms (192 → 215) và p95 gần như không đổi, nhưng p99 nhảy từ 232 ms lên 1667 ms. Với 10% (> 5% nên lọt vào p95), p95 cũng nhảy lên 1685 ms (gấp ~7.7 lần) trong khi avg mới tăng ~146 ms (1.76 lần) và p50 vẫn ~195 ms. Average **che giấu** nhóm user bị chậm: avg 338 ms trông vẫn "ổn", nhưng thực tế cứ 10 người thì 1 người chờ ~1.7 s. SLO đặt theo percentile mô tả đúng trải nghiệm của X% người dùng tệ nhất, còn average bị kéo bởi số đông request nhanh. Lần chạy 0.1 bị `load_test.py` trả exit code 1 vì vi phạm SLO p95 — đúng thứ ta muốn CI bắt được.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | **100% (23/23)** |
| Avg tokens / câu | 57.9 | 109.0 | 116.9 |
| Tổng cost (USD) | 0.000415 | 0.000760 | 0.000818 |
| Cost / câu đúng (USD) | 0.00041475 | 0.00003619 | 0.00003557 |
| Latency p50 / p95 (ms) | 171.0 / 187.6 | 191.6 / 216.8 | 185.4 / 220.2 |
| Quality gate | ❌ FAIL (min 80%) | ✅ PASS (min 80%, 0 regression vs v1) | ✅ PASS (min 100%, 0 regression vs v2) |

File: `reports/eval_v1.json`, `eval_v2.json`, `eval_v2_fixed.json`.

**Những câu v2 trả lời sai ban đầu và cách sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` — "JWT khác gì API key trong xác thực?" | `token`, `hết hạn`, `chữ ký` | KB không có tài liệu nào chứa từ khoá của câu hỏi → retrieval rỗng → trả câu chung chung. Thêm entry `(["jwt", "json web token"], …)` giải thích JWT là **token** tự chứa user + thời điểm **hết hạn**, được ký bằng secret, server kiểm **chữ ký**; so sánh với API key. |
| `scaling-03` — "Kubernetes HPA scale dựa trên những chỉ số nào?" | `cpu`, `memory`, `custom metrics` | Tương tự, không có entry nào khớp. Thêm entry `(["hpa", "horizontal pod autoscaler", "kubernetes", "autoscal"], …)`: HPA scale theo **CPU** utilization, **memory** hoặc **custom metrics** (request/s, queue) từ Prometheus. |

Trước khi thêm, em kiểm tra trong `golden_dataset.jsonl` không câu nào khác chứa `jwt`/`hpa`/`kubernetes`, để entry mới không "cướp" retrieval của câu đang đúng. Kết quả `--baseline reports/eval_v2.json`: **2 câu cải thiện, 0 regression**. Sửa ở `develop/utils/mock_llm.py` và chép sang `production/utils/mock_llm.py` để stack Docker cũng đúng 23/23.

**Quality gate trong CI:** `run_eval.py` trả **exit code 1** khi accuracy < `--min-accuracy` hoặc có câu regression so với `--baseline`. Chạy thử v1 với `--baseline reports/eval_v2.json` → FAIL, 20 câu regression, **exit code 1**. Job GitHub Actions ở [`.github/workflows/eval-quality-gate.yml`](.github/workflows/eval-quality-gate.yml): khởi động agent v2 (`MOCK_ERROR_RATE=0` để lỗi mạng giả lập không làm gate fail ngẫu nhiên), chạy `run_eval.py --min-accuracy 1.0 --baseline reports/eval_v2.json`; step lỗi → job lỗi → bật *Require status checks to pass* trong branch protection của `main` thì PR không merge được.

**Trade-off chất lượng / chi phí / latency giữa v1 và v2:** v2 thêm retrieval (RAG) nên prompt dài hơn: token mỗi câu ~1.9 lần (57.9 → 109.0), tổng cost ~1.8 lần, p95 eval chậm hơn ~29 ms (retrieval ~20 ms + LLM xử lý prompt dài hơn). Đổi lại accuracy tăng từ 4.3% lên 91.3%, nên **cost trên mỗi câu đúng rẻ hơn ~11.5 lần** (0.000415 → 0.0000362). So "cost / câu" thì v1 rẻ hơn, nhưng câu trả lời sai là vô giá trị (thậm chí có hại); "cost / câu đúng" mới phản ánh giá trị thật người dùng nhận được. Sau khi sửa KB, cost tăng nhẹ (+7.9 token/câu vì 2 câu giờ có context) nhưng cost/câu đúng còn giảm thêm.

---

## 4. Observability stack (production)

> **Sửa lỗi để stack chạy được:** lần đầu `docker compose up`, container agent restart liên tục với `ModuleNotFoundError: No module named 'uvicorn'`. Dockerfile cài package bằng `pip install --user` vào `/root/.local` rồi copy sang `/home/agent/.local`, nhưng user `agent` được tạo với home `-d /app` → Python tìm user site ở `/app/.local`. Đã thêm `ENV PYTHONUSERBASE=/home/agent/.local` vào `production/Dockerfile` (đúng lỗi CODE_LAB cảnh báo ở Part 6 Step 8).

Kiểm tra nhanh qua Nginx:

```text
$ curl -s -i -X POST http://localhost:8080/ask -H "X-API-Key: day14-lab-key" -H "Content-Type: application/json" -d '{"question": "Trong distributed tracing, span là gì?"}'
HTTP/1.1 200 OK
x-trace-id: 6dce0e79acd2149db2bd80bec2181d66
X-Served-By: 172.19.0.7:8000
{"answer":"Distributed tracing ghi lại một request đi qua những bước nào. ...","model":"mock-llm-small","agent_version":"v2","usage":{"input_tokens":81,"output_tokens":46,"cost_usd":3.975e-05},...}

$ curl -i http://localhost:8080/metrics
HTTP/1.1 404 Not Found
```

Unit test alert rules: `promtool test rules /p/alert_rules_test.yml` → **SUCCESS**.

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải — [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png)
- [ ] Một trace chậm trong Jaeger — [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [ ] Một trace lỗi trong Jaeger (có event `exception`) — [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)
- [ ] Trang Alerts của Prometheus với ≥ 2 alert **firing** — [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png)

**Exercise 7.4 — đọc dashboard:**
- **Khoảng cách avg ↔ p95/p99:** panel latency cho thấy avg ~238 ms, p50 ~190 ms, p95 ~240 ms nhưng p99 ~1.7 s. ~3% request bị mock LLM làm chậm +1.5 s chỉ kéo avg lên vài chục ms nhưng quyết định p99 → đúng như 2.2, phải nhìn percentile.
- **Input token > output token:** v2 nhét system prompt + tài liệu retrieval + câu hỏi vào prompt (vd 81 input vs 46 output token ở request trên). Context RAG làm input dài hơn câu trả lời; vì giá output đắt hơn input nên cost vẫn chịu ảnh hưởng của cả hai.
- **Vì sao `/metrics` bị chặn ở Nginx (404):** `/metrics` lộ thông tin nội bộ (số request, lỗi, version, model, chi phí) và không có auth; Prometheus scrape trực tiếp từng container qua network nội bộ (`dns_sd_configs` → `agent:8000`), không cần đi qua Nginx. Chặn ở Nginx = chỉ mạng nội bộ đọc được, đồng thời mỗi scrape đi thẳng vào đúng 1 instance (qua Nginx sẽ round-robin, mỗi lần scrape trúng instance khác → số liệu nhảy lung tung).

### 4.2 Thí nghiệm "phá hệ thống"

**Cách đo:** tải nền Locust headless **10 user** chạy suốt thí nghiệm (≈ 5–6 req/s). Em dùng 10 thay vì 30 user vì ở 30 user chi phí với `mock-llm-small` đã ≈ $2.9/giờ (> ngưỡng $2/giờ của `LLMCostBurnRateHigh`) → alert cost firing sẵn từ đầu và thí nghiệm C không đo được gì; ở 10 user cost nền ≈ $0.63/giờ. Mỗi thí nghiệm: chờ tới khi không còn alert nào → ghi `.env` → `docker compose -p k5-day14-observability up -d --scale agent=2 agent` (mốc t0) → poll `GET /api/v1/alerts` mỗi 5 s, ghi lúc alert sang `pending` và `firing` → trả `.env` về mặc định. Prometheus `scrape_interval` = `evaluation_interval` = 5 s.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (value 22.0%) | pending sau 32 s → **firing sau 92 s** (`for: 1m`) | **Error rate** đỏ: 0.57% → 17.9%; req/s theo status thấy 503 tăng. p95 không đổi (0.29 s) vì lỗi trả về sớm |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (value 1.92 s) | pending sau 32 s → **firing sau 152 s** (`for: 2m`) | **Latency p50/p95/p99**: p95 0.30 s → 1.92 s, trong khi avg chỉ 0.25 s → 0.68 s; error rate không đổi |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (value $6.66/giờ) | pending sau 69 s → **firing sau 189 s** (`rate[5m]` cần thời gian tích luỹ + `for: 2m`) | **Cost USD/giờ**: $0.63 → $7.64/giờ (≈ 12× sau 3.5 phút; cửa sổ 5 phút chưa đầy nên chưa lên đủ ~16×); LLM p95 tăng (p95 `/ask` 0.29 s → 0.70 s, avg 0.20 → 0.49 s) |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` (`up == 0`) | pending sau 15 s → **firing sau 45 s** (`for: 30s`) | **Instances up** 2 → 1. Panel Error rate của agent vẫn **0%** trong khi Nginx log có **11 lỗi 504** (`upstream timed out … while reading response header`, chờ `proxy_read_timeout 10s`) |
| E | `AGENT_VERSION=v1` | **Không có** (theo dõi 4 phút) | — | Retrieval hit ratio **mất dữ liệu** (v1 không retrieval), **tokens/s giảm** 655 → 363, cost $0.63 → $0.43/giờ, p95 còn *giảm* 0.29 → 0.20 s; bảng version đổi sang `v1`. Mọi Golden Signal đều "đẹp hơn" |

**Quan sát thêm:**
- **D — vì sao 504 không hiện trên dashboard:** request bị kẹt ở instance đang pause không bao giờ được agent xử lý xong, nên không được đếm vào `http_requests_total`; lỗi chỉ tồn tại ở Nginx (và phía client/Locust). `proxy_next_upstream error timeout` chuyển request **sau** khi timeout, nhưng với POST Nginx không retry (không idempotent) nên client nhận 504. Vì sao `pause` chứ không `stop`: Prometheus tìm agent bằng DNS; container `stop` biến mất khỏi DNS → target bị gỡ chứ không "down" → không có alert; container `pause` vẫn còn trong DNS nhưng không trả lời → `up == 0`.
- **Đổi cấu hình bằng `docker compose up -d` gây downtime:** lúc áp dụng `AGENT_VERSION=v1` (thí nghiệm E, 09:56:07 UTC), Nginx log **86 lỗi 502** trong ~1.5 phút sau đó: compose dừng cả 2 container agent cũ rồi mới tạo container mới (không rolling update), Nginx còn giữ IP cũ. Production cần rolling deploy (tạo instance mới, chờ `/ready`, rồi mới tắt instance cũ).

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đây là lỗi **chất lượng**, không phải lỗi hạ tầng: request vẫn 200, nhanh hơn, rẻ hơn, nên mọi alert dựa trên error/latency/cost đều im lặng, trong khi accuracy thực tế tụt từ 91% (v2) xuống 4% (v1) như đo ở mục 3. Cách bắt:
1. **Chặn trước khi deploy:** quality gate trong CI (`run_eval.py --baseline`, exit code 1 → không merge/không deploy). Lần này v1 bị chặn với 20 câu regression.
2. **Giám sát chất lượng khi chạy:** alert trên metric gần với chất lượng — retrieval hit ratio giảm đột ngột (panel đã mất dữ liệu ở E), `agent_info{version}` đổi ngoài lịch deploy, token/câu giảm mạnh; chạy **eval định kỳ** (canary/synthetic) trên golden set với production rồi alert khi accuracy < SLO.
3. **Tín hiệu từ người dùng:** tỉ lệ 👍/👎, tỉ lệ hỏi lại, sample câu trả lời cho LLM-as-judge hoặc người review.

### 4.3 Debug bằng trace

Trace chậm nhất trong 1 giờ chạy (tìm qua Jaeger API `/api/v3/traces`, `duration_min=1.5s`, 892 trace > 1.5 s):

- `trace_id`: `893a62fcd80b33f0ae3b882a9732c2e1`
- Tổng thời gian: **1876.5 ms** (`POST /ask`)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (1692.1 ms, 90.2%)** — `gen_ai.request.model=mock-llm-small`, `input_tokens=81`, `output_tokens=46`. Các span khác: `agent.run` 1782.0 ms (95.0%, span cha bao LLM), `agent.retrieve_context` 89.6 ms (4.8%). → Chậm nằm ở LLM upstream (request rơi vào nhóm `MOCK_SLOW_RATE`), không phải code của agent; muốn cải thiện phải đặt timeout/retry cho LLM, cache, hoặc đổi model/nhà cung cấp.

```text
POST /ask                    1876.5 ms  100.0%
└── agent.run                1782.0 ms   95.0%
    ├── agent.retrieve_context  89.6 ms    4.8%
    └── chat mock-llm-small   1692.1 ms   90.2%   ← span chậm nhất
```

- Dòng log tương ứng (grep theo `trace_id` trong `docker compose -p k5-day14-observability logs agent`):

```json
{"ts": "2026-11-25T09:37:52.021509+00:00", "level": "INFO", "service": "observable-agent", "instance": "5949f38237d2", "event": "llm_call", "trace_id": "893a62fcd80b33f0ae3b882a9732c2e1", "span_id": "408444a2da1a78d4", "request_id": "b43c9b349747", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 81, "output_tokens": 46, "cost_usd": 4e-05, "llm_ms": 1692.5}
{"ts": "2026-11-25T09:37:52.022223+00:00", "level": "INFO", "service": "observable-agent", "instance": "5949f38237d2", "event": "http_request", "trace_id": "893a62fcd80b33f0ae3b882a9732c2e1", "span_id": "408444a2da1a78d4", "request_id": "b43c9b349747", "method": "POST", "route": "/ask", "status": 200, "ms": 1875.1}
```

Trace lỗi: `trace_id` `a12314ec93049153ee2cfac0e7ba88cb` (115.9 ms). Span `chat mock-llm-small` và `agent.run` có status ERROR, event `exception` với `exception.type = utils.mock_llm.LLMError`, `exception.message = "Upstream LLM error: 503 Service Unavailable (mock)"`. Log tương ứng:

```json
{"ts": "2026-11-25T09:44:17.756784+00:00", "level": "INFO", "service": "observable-agent", "instance": "4d3e21bf7d3b", "event": "llm_error", "trace_id": "a12314ec93049153ee2cfac0e7ba88cb", "span_id": "d58709111e0cb763", "request_id": "ae71ad65e539", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-25T09:44:17.757122+00:00", "level": "INFO", "service": "observable-agent", "instance": "4d3e21bf7d3b", "event": "http_request", "trace_id": "a12314ec93049153ee2cfac0e7ba88cb", "span_id": "d58709111e0cb763", "request_id": "ae71ad65e539", "method": "POST", "route": "/ask", "status": 503, "ms": 115.2}
```

(Nhận xét nhỏ: dòng `llm_error` có `"level": "INFO"` — nên log ở mức ERROR để lọc/alert theo level.)

---

## 5. Benchmark scaling (production, Locust headless, 100 users, ramp 10/s, 2 phút)

| Số instance | RPS (`/ask`) | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 60.2 | 190 | 240 | 1700 | 80 / 7189 (1.11%) |
| 3 | 60.07 | 190 | 230 | 1700 | 66 / 7176 (0.92%) |

File: `benchmark-results/scale_1_stats.csv`, `scale_3_stats.csv` (dòng `POST,/ask`; dòng `Aggregated` gồm cả `/health`: 66.21 và 65.95 RPS).

> **Lưu ý cách đo:** lần chạy đầu tiên em dùng đúng lệnh `docker compose ... run --rm locust ...`, nhưng `docker compose run` khởi động lại các service phụ thuộc (`locust → nginx → agent`) và đưa `agent` về `deploy.replicas: 2` → cả 2 lần đo thực chất đều là 2 instance (Prometheus chỉ thấy traffic ở 2 instance). Em đo lại với `run --rm --no-deps` và kiểm tra số instance trước/sau mỗi lần chạy (1 → 1, 3 → 3). Trong lần chạy 3 instance, Prometheus `sum by (instance) (rate(http_requests_total{route="/ask"}[1m]))` cho 20.8 / 20.5 / 20.4 req/s → Nginx chia đều.

**Giải thích kết quả:** Scale 1 → 3 **không** cải thiện RPS hay p95 (60.2 vs 60.1 RPS; p95 240 vs 230 ms — nằm trong dao động). Có 2 lý do:

1. **Tải bị giới hạn bởi load generator, không phải server.** Locust là mô hình *closed-loop*: 100 user, mỗi user `wait_time = between(0.5, 2)` (trung bình 1.25 s) giữa các request. Mỗi user gửi ≈ 1 / (1.25 + 0.24) ≈ 0.67 req/s → 100 user ≈ 67 req/s tổng — khớp con số đo được (66 RPS aggregated). 1 instance async đã phục vụ thoải mái 60 req/s, nên thêm instance không có thêm việc để làm.
2. **Latency bị chặn dưới bởi LLM.** p50 ~190 ms gần như toàn bộ là thời gian chờ mock LLM (120 ms + jitter + token) và p99 ~1.7 s là các request rơi vào `MOCK_SLOW_RATE`. Thêm instance không làm LLM trả lời nhanh hơn → p95/p99 không đổi. Nút thắt latency nằm ở **LLM upstream**, không phải CPU của agent.

Scale ngang chỉ có ích khi instance đang bão hoà (CPU, event loop, connection) — muốn thấy khác biệt phải tăng tải (nhiều user hơn, `wait_time` ngắn hơn) cho tới khi 1 instance có p95 tăng/lỗi tăng. Còn để giảm latency thì phải xử lý ở LLM: cache câu trả lời, streaming, model nhỏ hơn, timeout + retry.

---

## 6. Kết luận

- 3 điều học được về benchmark & observability:
  1. **Average nói dối:** 3% request chậm gần như không đổi avg nhưng đẩy p99 lên ~7 lần; SLO và alert phải theo percentile.
  2. **Phải nghi ngờ chính công cụ đo:** load generator 1 process nghẽn ở concurrency 100; `docker compose run` âm thầm đổi số replica. Luôn kiểm tra điều kiện thí nghiệm (số instance, ai là nút thắt) trước khi kết luận.
  3. **Metric hạ tầng không thấy lỗi chất lượng:** đổi sang v1 làm accuracy tụt từ 91% xuống 4% mà dashboard vẫn xanh; cần eval/quality gate trong CI và giám sát chất lượng khi chạy thật. Ba trụ cột metrics → traces → logs nối với nhau qua `trace_id` giúp đi từ "p95 tăng" tới "span LLM chậm" tới "dòng log cụ thể" rất nhanh.
- Nếu đưa agent này lên production thật, em sẽ thêm:
  - Giám sát ở tầng ngoài: synthetic check từ bên ngoài gọi `/ask` định kỳ và metric/log của Nginx (502/504, `upstream_response_time`) — lỗi ở LB không hiện trên metric của agent.
  - Metric chất lượng: online eval định kỳ trên golden set, tỉ lệ retrieval hit, feedback 👍/👎 của user; alert khi retrieval hit ratio giảm (bắt được thí nghiệm E).
  - Alert theo **error budget burn rate** (multi-window, vd 1h và 6h) thay cho ngưỡng tức thời; alert khi `llm_requests_total{status="error"}` tăng theo từng model/provider.
  - Alert chi phí theo ngày/tháng so với budget thật (không chỉ burn rate), và theo dõi `agent_in_flight_requests` (saturation) để biết khi nào cần scale.
