FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /

# 整包复制（app/ 是一个包：main.py + protocols.py + relay.py + admin.py + store.py + channels/ + static/）
COPY app /app
RUN pip install -r /app/requirements.txt

EXPOSE 18673
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:18673/healthz',timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18673", "--no-access-log"]
