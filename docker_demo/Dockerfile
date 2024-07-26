FROM python:3-alpine
ENV PYTHONUNBUFFERED 1
RUN apk add git && git clone https://github.com/feelancer21/feelancer /feelancer
WORKDIR /feelancer
RUN git checkout "master"
RUN pip install -r requirements.txt .
ENTRYPOINT [ "sh" ]
