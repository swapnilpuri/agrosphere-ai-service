FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src src
COPY alembic.ini ./
COPY migrations migrations
RUN pip install --no-cache-dir .
USER 10001:10001
EXPOSE 8086
CMD ["uvicorn", "agrosphere_ai.main:app", "--host", "0.0.0.0", "--port", "8086"]
