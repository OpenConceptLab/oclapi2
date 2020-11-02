FROM python:3.8.3
ENV PYTHONUNBUFFERED 1
RUN mkdir /code
ADD requirements.txt /code/
WORKDIR /code
RUN pip install packaging
RUN pip install -r requirements.txt
ADD . /code/
RUN chmod +x wait_for_it.sh
RUN chmod +x startup.sh
RUN chmod +x start_celery_worker.sh
RUN chmod +x start_flower.sh
EXPOSE 8000
CMD ["bash", "-c", "startup.sh"]
