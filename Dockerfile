FROM alpine AS build-base
RUN apk --no-cache upgrade
RUN apk --no-cache add alpine-sdk python3 python3-dev libxml2-dev libxslt-dev
RUN python3 -m venv /app/venv && \
    /app/venv/bin/pip install --upgrade pip && \
    /app/venv/bin/pip install --upgrade setuptools wheel


FROM build-base AS build-static
RUN /app/venv/bin/pip install css-html-js-minify
COPY ./static/ /app/static/
RUN /app/venv/bin/css-html-js-minify --comments --overwrite /app/static


FROM build-base AS flake8
RUN /app/venv/bin/pip install --upgrade flake8
COPY ./.flake8 /usr/src/obra-hacks/.flake8
COPY ./python/ /usr/src/obra-hacks/python/
WORKDIR /usr/src/obra-hacks/
RUN /app/venv/bin/flake8 /usr/src/obra-hacks/python/


FROM build-base AS build-python
ARG SQLITE_VERSION="3.36.0"
ARG APSW_VERSION="${SQLITE_VERSION}-r1"
ARG CFLAGS="-DSQLITE_USE_ALLOCA=1 -DSQLITE_DQS=0 -DSQLITE_LIKE_DOESNT_MATCH_BLOBS=1 -DSQLITE_OMIT_AUTOINIT=1 -Wno-sign-compare -Wno-unused-function -Wno-unused-variable -Wno-maybe-uninitialized -Wno-deprecated-declarations -Wno-pointer-sign"
ADD https://github.com/rogerbinns/apsw/archive/${APSW_VERSION}.tar.gz /usr/src/
RUN tar -zxvf /usr/src/${APSW_VERSION}.tar.gz -C /usr/src/
WORKDIR /usr/src/apsw-${APSW_VERSION}
RUN /app/venv/bin/python setup.py fetch --all --version=${SQLITE_VERSION}
RUN /app/venv/bin/python setup.py build --enable-all-extensions install
RUN /app/venv/bin/python setup.py test
COPY ./python/requirements.txt /usr/src/obra-hacks/python/requirements.txt
RUN /app/venv/bin/pip install -r /usr/src/obra-hacks/python/requirements.txt
COPY ./python/ /usr/src/obra-hacks/python/
RUN /app/venv/bin/pip install --no-deps --use-feature=in-tree-build /usr/src/obra-hacks/python/ && \
    cp -v /usr/src/obra-hacks/python/app/* /app/
RUN (CACHE_TYPE=SimpleCache timeout 5 /app/venv/bin/python /app/obra-hacks.py & sleep 2 && curl -vs http://127.0.0.1:5000/api/v1/events/years/)
COPY docker-entrypoint.sh /app/
RUN mkdir -p /data /tmp/spool /tmp/tls


FROM scratch AS build-collect
COPY --chown=405:100 --from=build-static /app/ /app/
COPY --chown=405:100 --from=build-python /app/ /app/
COPY --chown=405:100 --from=build-python /data/ /data/
COPY --chown=405:100 --from=build-python /tmp/ /tmp/
COPY --chown=405:100 ./conf/ /app/conf/


FROM alpine
RUN apk --no-cache upgrade && apk --no-cache add libxml2 libxslt python3 bash libstdc++ openssl uwsgi-http uwsgi-python3 uwsgi-router_static
COPY --from=build-collect / /
LABEL maintainer="Brad Davidson <brad@oatmail.org>"
VOLUME ["/tmp"] 
VOLUME ["/data"]
USER guest
ENV UWSGI_CERT=/tmp/tls/server.pem UWSGI_KEY=/tmp/tls/server.key CACHE_TYPE=uwsgi HOME=/data
EXPOSE 8080 8443
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uwsgi", "--yaml", "/app/conf/uwsgi.yaml"]
