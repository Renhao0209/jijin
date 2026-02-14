# MoneyWatch Backend (FastAPI)

个人自用的基金估值与均线服务端，提供统一接口给 Flutter 前端。

## 本地运行

1) 安装依赖（Windows PowerShell）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2) 启动服务

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 服务器部署（Ubuntu 20.04+ / systemd + Gunicorn）

0) 下载/更新代码（在服务器上）

首次下载：

```bash
sudo apt update
sudo apt install -y git

sudo mkdir -p /opt/moneywatch
cd /opt/moneywatch
sudo git clone https://github.com/Renhao0209/jijin.git backend

# 建议把目录所有者切到当前用户，后续 pip/编辑文件更方便
sudo chown -R $USER:$USER /opt/moneywatch/backend
cd /opt/moneywatch/backend
ls
```

后续更新：

```bash
cd /opt/moneywatch/backend
git pull
```

注意：`pip install -r requirements.txt` 必须在包含 `requirements.txt` 的项目目录执行（例如 `/opt/moneywatch/backend`）。

1) 安装 Python 与依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

2) 创建虚拟环境并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gunicorn
```

3) 配置环境变量（可选）

```bash
cp .env.example .env
```

4) 创建 systemd 服务

将下面内容保存为 `/etc/systemd/system/moneywatch-backend.service`：

注意：

- `User=` 必须是服务器上真实存在的用户；如果你全程用 root 部署，可以先写 `User=root` 跑通再优化。
- 如果你不确定 `.env` 是否存在，建议把 `EnvironmentFile` 写成可选（前面加 `-`），避免因为 `.env` 缺失导致服务启动失败。

```ini
[Unit]
Description=MoneyWatch Backend
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/moneywatch/backend
EnvironmentFile=-/opt/moneywatch/backend/.env
ExecStart=/opt/moneywatch/backend/.venv/bin/gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000 --workers 2
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启动并设置开机自启：

```bash
sudo systemctl daemon-reload
sudo systemctl enable moneywatch-backend
sudo systemctl start moneywatch-backend
sudo systemctl status moneywatch-backend
```

如果 `curl http://127.0.0.1:8000/...` 返回 `Connection refused`（8000 无监听），先排查：

```bash
sudo systemctl status moneywatch-backend --no-pager -l
sudo ss -lntp | grep ':8000' || echo "8000 没有监听"

# 看看 gunicorn 是否监听在 unix socket 上（有些环境会这样配置）
sudo ss -lxnp | grep gunicorn || true

# 常规情况下用 journalctl 看错误；如果你的系统禁用了 journald，需要改成输出到文件日志
sudo journalctl -u moneywatch-backend -n 200 --no-pager || true
```

## 反向代理（Nginx，可选）

```nginx
server {
  listen 80;
  server_name your.domain.com;

  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

## Docker 部署

1) 准备环境变量

```bash
cp .env.example .env
```

2) 构建并启动

```bash
docker compose up -d --build
```

3) 查看日志

```bash
docker compose logs -f
```

## 环境变量

- `BACKEND_ALLOWED_ORIGINS`：CORS 白名单，逗号分隔，例如：
  - `http://localhost:3000,http://localhost:5173`
- `BACKEND_CACHE_TTL_EST`：实时估值缓存秒数（默认 3）
- `BACKEND_CACHE_TTL_NAV`：历史净值缓存秒数（默认 3600）
- `BACKEND_WATCH_CODES`：预热/收盘任务基金列表（逗号分隔）
- `TUSHARE_TOKEN`：Tushare Pro Token（可选，用于“tushare”历史净值源）
- `XUEQIU_COOKIE`：雪球 Cookie（可选，用于“xueqiu”实时估值源）
- `AKSHARE_ENABLED`：是否启用 AkShare（`0/1`，可选，用于“akshare”实时估值源；同时需要安装 `akshare`）
- `JOINQUANT_TOKEN`：占位（当前未接入）
- `RICEQUANT_TOKEN`：占位（当前未接入）

## 数据源维护（需要你申请/准备的 token）

不需要 token（默认可用）：

- `fundgz`：实时估值（第三方 JSONP）
- `eastmoney`：历史净值
- `pingzhong`：历史净值兜底

需要你提供凭证/开关（可选）：

- `tushare`：需要 `TUSHARE_TOKEN`
  - 你需要做的：去 Tushare Pro 官网注册/开通，拿到 Token
  - 服务端操作：把 Token 写入 `.env` 或 systemd 的 `EnvironmentFile`，然后重启后端
- `xueqiu`：需要 `XUEQIU_COOKIE`
  - 你需要做的：用浏览器登录 xueqiu.com，从请求头复制完整 Cookie（通常会包含 `xq_a_token` 等字段）；Cookie 可能会过期，需要定期更新
  - 服务端操作：写入 `.env` 并重启后端
- `akshare`：不需要 token，但需要启用与依赖
  - 你需要做的：不需要申请 token
  - 服务端操作：`AKSHARE_ENABLED=1`，并在环境中安装 `akshare`（可选，建议默认关闭）

占位（未接入，不需要你申请 token）：

- `joinquant` / `ricequant`：当前只是预留字段，后端未实现对应拉取逻辑

验证方式：

- 调用 `GET /api/data/source-list` 查看哪些源 `ok=true`
- 修改 `.env` 后要重启后端（systemd：`sudo systemctl restart moneywatch-backend`；Docker：`docker compose restart`）

### 启用“全部已实现数据源”（推荐配置）

说明：`joinquant/ricequant` 当前是占位未接入，无法真正启用；其余已实现的源可以全部打开。

把下面这些写进你服务器上的 `.env`（不要提交到 git）：

- `TUSHARE_TOKEN=你的token`
- `XUEQIU_COOKIE=你的cookie`（需要你自己抓；不填则 xueqiu 源会显示未配置）
- `AKSHARE_ENABLED=1`

然后执行：

- systemd：`sudo systemctl restart moneywatch-backend`
- Docker：`docker compose up -d --build`

最后用 `GET /api/data/source-list` 检查：`tushare/akshare` 是否 `ok=true`，`xueqiu` 是否已配置。

## 接口

- `GET /api/real-time/estimate?codes=110022,161725`
- `GET /api/chart/pro-trend/{code}`
- `GET /api/history/ma-line/{code}`
- `POST /api/hold/profit`
- `GET /api/data/source-list`
- `GET /api/trade/status`

备注：已支持 fundgz、eastmoney、pingzhong 以及可选的 tushare / xueqiu / akshare；JoinQuant / RiceQuant 为占位。

## 定时任务

- 交易日 9:00/13:00 预热数据源
- 交易日 20:00 拉取历史净值刷新缓存
- 每日 0:00 清理缓存
