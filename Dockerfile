FROM python:3.12-slim

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir .

ENV NZB_DIR=/nzbs
ENV HOST=0.0.0.0
ENV PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn nzbserver.app:app --host ${HOST} --port ${PORT}"]
