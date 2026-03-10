FROM python:3.10-slim AS builder
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    bash git gcc libssl-dev libffi-dev libpq-dev python3-dev build-essential cargo

RUN pip install --upgrade pip

RUN mkdir /code
WORKDIR /code
ADD requirements.txt /code/

RUN pip wheel --no-cache-dir --wheel-dir /code/wheels -r requirements.txt

FROM python:3.10-slim
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

RUN groupadd -r ocl && useradd -r -g ocl ocl
RUN mkdir /home/ocl
RUN chmod -R 0777 /home/ocl

ENV APP_HOME=/code

RUN mkdir -p $APP_HOME /temp /staticfiles /uploads && \
    chown -R ocl:ocl $APP_HOME /temp /staticfiles /uploads

WORKDIR $APP_HOME

RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    libpq-dev bash curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip

COPY --from=builder /code/wheels /wheels
COPY --from=builder /code/requirements.txt .
RUN pip install --no-cache-dir /wheels/*
RUN rm -rf /wheels

ADD --chown=ocl:ocl . $APP_HOME

RUN python manage.py collectstatic --noinput

USER ocl

# Normalize Windows CRLF -> LF for shell scripts checked out on Windows
RUN sed -i 's/\x0D$//' set_build_version.sh startup.sh wait_for_it.sh pre_startup.sh start_celery_worker.sh ping_celery_worker.sh start_flower.sh start_celery_beat.sh

RUN chmod +x set_build_version.sh wait_for_it.sh startup.sh start_celery_worker.sh ping_celery_worker.sh start_flower.sh start_celery_beat.sh

ARG SOURCE_COMMIT

RUN ["bash", "set_build_version.sh"]

EXPOSE 8000

CMD ["bash", "startup.sh"]