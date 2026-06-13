# Oculory docker-stats exporter — pure stdlib, no pip deps. Multi-arch (ARM64 ok).
FROM python:3.12-alpine
WORKDIR /app
COPY exporter.py .
EXPOSE 9101
CMD ["python", "-u", "exporter.py"]
