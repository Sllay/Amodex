FROM python:3.13.5-slim-bookworm

WORKDIR /app

RUN pip install --no-cache-dir "psycopg[binary]==3.2.10"

COPY research/p0-06-northflank/app.py /app/app.py

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

EXPOSE 8080

CMD ["python", "/app/app.py"]
