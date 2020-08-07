FROM python:3.8.3
ENV PYTHONUNBUFFERED 1
RUN mkdir /code
ADD requirements.txt /code/
WORKDIR /code
RUN pip install -r requirements.txt
ADD . /code/
EXPOSE 8000
CMD bash startup.sh
