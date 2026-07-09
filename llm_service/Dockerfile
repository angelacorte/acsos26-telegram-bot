FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY llm_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY llm_service /app/llm_service
COPY src/main/resources/acsos26/conference.json /app/conference.json

ENV CONFERENCE_DATA=/app/conference.json
EXPOSE 8000
CMD ["uvicorn", "llm_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
