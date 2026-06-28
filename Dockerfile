FROM python:3.12-slim

WORKDIR /app

COPY server.py /app/server.py
COPY static /app/static
COPY config/videos.example.json /app/config/videos.example.json

ENV HOST=0.0.0.0
ENV PORT=8787

EXPOSE 8787

CMD ["python3", "/app/server.py"]
