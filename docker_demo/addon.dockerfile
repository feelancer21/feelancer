FROM python:3.10.8

RUN apt-get update && \
    apt-get -y upgrade && \
    apt-get install -y build-essential bash-completion && \
    python -m pip install --upgrade pip

RUN git clone https://github.com/feelancer21/feelancer /feelancer
WORKDIR /feelancer

RUN git checkout "master"
RUN pip install -r addon-requirements.txt .

ENV SHELL "/bin/bash"
ENTRYPOINT ["/bin/bash"]