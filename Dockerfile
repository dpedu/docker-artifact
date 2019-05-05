FROM ubuntu:bionic

RUN apt-get update && \
    apt-get install -y python3-pip gpgv1 gnupg1 gpg sudo wget git

ADD . /tmp/code

RUN cd /tmp/code && \
    pip3 install -r requirements.txt && \
    python3 setup.py install && \
    useradd repobot

USER repobot

ENTRYPOINT ["repobotd"]
