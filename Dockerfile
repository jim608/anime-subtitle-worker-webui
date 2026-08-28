FROM node:22-bookworm-slim AS frontend

WORKDIR /frontend

COPY package*.json /frontend/
RUN npm ci

ARG SOURCE_REVISION=unknown
RUN printf '%s\n' "$SOURCE_REVISION" > /frontend/.source-revision

COPY index.html vite.config.js /frontend/
COPY Dockerfile /frontend/Dockerfile
COPY src /frontend/src
COPY tests /frontend/tests
RUN npm run test:frontend \
    && npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

ARG SOURCE_REVISION=unknown
RUN printf '%s\n' "$SOURCE_REVISION" > /app/.source-revision

COPY app.py /app/app.py
COPY control_api.py /app/control_api.py
COPY --from=frontend /frontend/dist /app/static

EXPOSE 8765

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]
