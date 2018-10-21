FROM ubuntu:bionic

RUN apt-get update && \
    apt-get install -y python3-pip gpgv1 gnupg1 gpg sudo wget

RUN cd /tmp && \
    wget -qO aptly.tgz https://bintray.com/artifact/download/smira/aptly/aptly_1.3.0_linux_amd64.tar.gz && \
    tar xvf aptly.tgz aptly_1.3.0_linux_amd64/aptly && \
    mv aptly_1.3.0_linux_amd64/aptly /usr/bin/ && \
    rm -rf aptly.tgz aptly_1.3.0_linux_amd64

ADD . /tmp/code

RUN cd /tmp/code && \
    python3 setup.py install && \
    useradd repobot && \
    rm -rf /tmp/code

ADD start /start

ENTRYPOINT ["/start"]
