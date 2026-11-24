FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
CMD ["python", "bot.py"]
