# relay-proxy · 中转站本地代理

**真密钥不出本机。** 一个零依赖的小代理，解决 5 个实际问题。

---

## 解决什么

| 问题 | 代理怎么解决 |
|---|---|
| 密钥要填进各种客户端/网页/插件，容易泄漏 | 客户端只填 `local`，真密钥留在代理里 |
| 局域网/多设备要共用一份密钥 | 监听 `0.0.0.0`，手机平板电视都能连 |
| 客户端写死了模型名，但你的站点没有那个模型 | 模型映射，`gpt-4=deepseek-chat` 一行搞定 |
| 想知道谁在用、用了多少 | 请求日志 JSONL + 实时统计 |
| 网页应用调 API 被 CORS 拦 | 自动加 CORS 头 |

---

## 用法

```bash
# 本机使用
python relay_proxy.py -u https://api.example.com/v1 -k sk-真实密钥

# 局域网共享（其他设备也能用）
python relay_proxy.py -u https://api.example.com/v1 -k sk-真实密钥 --host 0.0.0.0

# 带模型映射 + 日志
python relay_proxy.py -u https://api.example.com/v1 -k sk-真实密钥 \
  -m "gpt-4=deepseek-chat,gpt-3.5-turbo=qwen-turbo" --log requests.jsonl
```

启动后，客户端里填：

```
API 地址   http://127.0.0.1:8788/v1
API 密钥   local
```

---

## 启动后长这样

```
════════════════════════════════════════════════════════════
  relay-proxy · 中转站本地代理  v1.0.0
════════════════════════════════════════════════════════════
  上游      https://api.example.com/v1
  监听      127.0.0.1:8788

  在各客户端里填：
    API 地址   http://127.0.0.1:8788/v1
    API 密钥   local      ← 客户端里填这个，真密钥不出本机

  局域网其他设备用： http://192.168.1.20:8788/v1
════════════════════════════════════════════════════════════
  Ctrl+C 停止
```

运行时的请求日志：

```
14:22:01 200 POST /v1/chat/completions deepseek-chat  1.82s in=1240 out=386
14:22:07 200 POST /v1/chat/completions gpt-4 -> deepseek-chat  0.94s in=88 out=52
14:23:11 401 POST /v1/chat/completions  (无效密钥)
```

---

## 状态接口

浏览器打开 `http://127.0.0.1:8788/health`：

```json
{
  "ok": true,
  "upstream": "https://api.example.com/v1",
  "uptime_sec": 3600,
  "requests": 128,
  "tokens_in": 45210,
  "tokens_out": 12890,
  "model_map": {"gpt-4": "deepseek-chat"}
}
```

---

## 参数

| 参数 | 说明 |
|---|---|
| `-u / --upstream` | 上游中转站地址（必填） |
| `-k / --key` | 真实密钥（必填，或环境变量 `RELAY_API_KEY`） |
| `--host` | 监听地址，默认 `127.0.0.1`；局域网用 `0.0.0.0` |
| `-p / --port` | 端口，默认 `8788` |
| `--local-key` | 客户端填的本地密钥，默认 `local` |
| `-m / --map` | 模型映射 `A=B,C=D` |
| `--log` | 请求日志写入 JSONL |

---

## 安全提示

- **监听 `0.0.0.0` 时，同网络的人都可能连上你的代理**。只在家里/公司内网用，别暴露到公网
- 代理不加密、不鉴权（本地密钥只是占位符）。公网使用请自行加反代 + HTTPS + 认证
- 日志文件含请求内容摘要，注意保管
- 本工具只做本机转发，不上传任何数据

---

## 相关项目

| 项目 | 用途 |
|---|---|
| [relay-bench](https://github.com/dafeiai918/relay-bench) | 测你的中转站行不行 |
| [relay-converter](https://github.com/dafeiai918/relay-converter) | 一份配置转 19 种客户端格式 |
| [OneClickRelay](https://github.com/dafeiai918/oneclickrelay) | 一键把中转站写进 60+ 客户端 |

---

## License

MIT
