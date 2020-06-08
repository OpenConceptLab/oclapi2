FROM python:3.8.3
ENV PYTHONUNBUFFERED 1
RUN mkdir /code
WORKDIR /code
ADD requirements.txt /code/
ADD startup.sh /code/
RUN apt-get update --fix-missing
RUN apt-get install -y software-properties-common
RUN pip install -r requirements.txt
CMD bash startup.sh
