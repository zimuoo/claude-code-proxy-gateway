FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py .
COPY config ./config

ENV PORT=8080
EXPOSE 8080

CMD ["python", "run.py"]
