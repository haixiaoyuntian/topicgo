# 仓库根目录专用：适配 Render / Zeabur 等从仓库根目录找 Dockerfile 的平台
# 项目在 student-topic-system/ 子目录，COPY 时带上该前缀
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY student-topic-system/src/requirements.txt ./student-topic-system/src/requirements.txt
RUN pip install --no-cache-dir -r student-topic-system/src/requirements.txt

COPY student-topic-system/src ./student-topic-system/src
COPY student-topic-system/website ./student-topic-system/website
COPY student-topic-system/prototype ./student-topic-system/prototype
COPY student-topic-system/wiki/screenshots ./student-topic-system/wiki/screenshots
COPY student-topic-system/README.md ./student-topic-system/README.md

EXPOSE 8080

CMD ["sh", "-c", "gunicorn --chdir student-topic-system/src --workers 1 --threads 4 --timeout 60 --bind 0.0.0.0:${PORT} app:app"]
