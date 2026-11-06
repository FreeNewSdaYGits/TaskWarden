FROM python:3.12-slim

RUN pip install --no-cache-dir apprise==1.13.1 requests

WORKDIR /app
COPY ntfy_to_apprise.py /app/ntfy_to_apprise.py

CMD ["python", "-u", "/app/ntfy_to_apprise.py"]
