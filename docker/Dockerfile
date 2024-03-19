FROM python:3.10-bullseye
RUN apt-get update && \
    apt-get install -y curl build-essential && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
RUN pip install maturin
ENV PATH="/root/.cargo/bin:${PATH}"

RUN git clone https://github.com/capnproto/capnproto.git \
    && cd capnproto/c++ \
    && git checkout v1.0.2 \
    && autoreconf -i \
    && ./configure \
    && make -j$(nproc) \
    && make install

RUN curl -sL https://deb.nodesource.com/setup_12.x | bash -
RUN apt-get install nodejs

RUN mkdir /opt/arborist/
COPY ./* /opt/arborist
COPY ./src /opt/arborist/src
RUN pip install /opt/arborist

RUN git config --global user.name Subsequent
RUN git config --global user.email software@subseq.io
RUN git config --global init.defaultBranch main

ENTRYPOINT ["arborist"]
