FROM python:3.13-slim
WORKDIR /app
COPY . .
ENV HOST=0.0.0.0 PORT=8080 AGENT_DB=/data/agents.db PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
CMD ["python", "server.py"]
