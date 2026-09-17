# ============================================================================
#  qlikeapi-plugins —— 常用开发/运维命令
#  make help 看全部
# ============================================================================
SHELL := /bin/bash
PY    ?= python3
VENV  ?= .venv
PORT  ?= 18673

.DEFAULT_GOAL := help
.PHONY: help install run test test-cov lint fmt check verify ui-diff docker-build up down logs restart backup clean

help: ## 显示所有可用命令
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## 建虚拟环境并装依赖（开发用）
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install -U pip
	$(VENV)/bin/pip install -r requirements-dev.txt

run: ## 本机直接跑起来（不走 docker，调试用）
	QLIKEAPI_DB=$${QLIKEAPI_DB:-./data/qlikeapi.db} $(VENV)/bin/uvicorn app.main:app --host 127.0.0.1 --port $(PORT) --reload

test: ## 跑单元测试（零网络，绝不打上游）
	$(VENV)/bin/pytest

test-cov: ## 跑测试并出覆盖率
	$(VENV)/bin/pytest --cov=app --cov-report=term-missing

lint: ## 静态检查（ruff）
	$(VENV)/bin/ruff check app tests scripts

fmt: ## 自动修复可修复的 lint 问题
	$(VENV)/bin/ruff check --fix app tests scripts

check: lint test ## 提交前必跑：lint + 测试

verify: ## 零成本验收：本地校验 / dry-run / 抹 prompt 探活（不打真实出图）
	$(PY) scripts/verify_v3.py

ui-diff: ## 新旧样式 A/B 计算样式比对（需 playwright + chromium；期望「合计差异: 0」）
	@# 前置：容器里要有一份「旧样式表」当对照组，重建镜像后会丢，需重新注入：
	@#   git stash 前先备份旧 style.css，然后 docker cp <旧style.css> qlikeapi-plugins:/app/static/style.css
	@curl -sf -o /dev/null http://127.0.0.1:$(PORT)/static/style.css || \
	  { echo "✗ 容器内缺 /static/style.css（旧样式对照组），先 docker cp 注入，否则比对全是假差异"; exit 2; }
	$(PY) scripts/ui_style_diff.py

docker-build: ## 构建镜像
	docker build -t qlikeapi-plugins:latest .

up: ## 起服务（需要先 cp docker-compose.example.yml docker-compose.yml）
	docker compose up -d --build

down: ## 停服务
	docker compose down

restart: ## 重启容器（改完代码常用）
	docker compose restart qlikeapi-plugins

logs: ## 跟随日志
	docker compose logs -f --tail=100 qlikeapi-plugins

backup: ## 备份 SQLite（含渠道密钥密文）
	@mkdir -p backups
	@cp data/qlikeapi.db backups/qlikeapi.db.$$(date +%Y%m%d-%H%M%S) && echo "已备份到 backups/"

clean: ## 清缓存
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null; rm -rf .pytest_cache .ruff_cache .coverage htmlcov; echo clean
