FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DB_PATH=/data/reminders.db
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# SQLite lives here — mount a volume so it survives redeploys.
VOLUME /data
CMD ["python", "-m", "app.bot"]
