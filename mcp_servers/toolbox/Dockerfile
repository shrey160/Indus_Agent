FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

RUN addgroup --system app && adduser --system --group app
USER app

EXPOSE 9000

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python /app/healthcheck.py

CMD ["python", "server.py"]
