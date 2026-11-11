FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY src/requirements.txt ./src/requirements.txt
RUN pip install --no-cache-dir -r src/requirements.txt

# 应用代码
COPY src ./src
# 项目主页、原型、截图（/home、/prototype、/guide、/screenshots 需要）
COPY website ./website
COPY prototype ./prototype
COPY wiki/screenshots ./wiki/screenshots
COPY README.md ./

EXPOSE 8000

# 单 worker + 多线程：SQLite 写锁下比多进程更稳
CMD ["sh", "-c", "gunicorn --chdir src --workers 1 --threads 4 --timeout 60 --bind 0.0.0.0:${PORT} app:app"]
