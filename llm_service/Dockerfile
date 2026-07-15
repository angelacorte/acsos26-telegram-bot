FROM python:3.13.5-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY llm_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY --chown=10001:10001 llm_service /app/llm_service
COPY --chown=10001:10001 src/main/resources/acsos26/conference.json /app/conference.json

ENV CONFERENCE_DATA=/app/conference.json
EXPOSE 8000
USER 10001:10001
CMD ["uvicorn", "llm_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
