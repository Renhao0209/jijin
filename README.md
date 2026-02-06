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

```ini
[Unit]
Description=MoneyWatch Backend
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/moneywatch/backend
EnvironmentFile=/opt/moneywatch/backend/.env
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
- `TUSHARE_TOKEN`：Tushare Pro Token（可选）
- `XUEQIU_COOKIE`：雪球 Cookie（可选，用于估值）
- `JOINQUANT_TOKEN`：JoinQuant Token（占位，可选）
- `RICEQUANT_TOKEN`：RiceQuant Token（占位，可选）
- `AKSHARE_ENABLED`：是否启用 AkShare（`0/1`，可选）

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
