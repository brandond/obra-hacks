#!/usr/bin/env bash

set -x

cd $(dirname $0) || exit

DOCKER_ORG=${DOCKER_ORG:-brandond}
DOCKER_TAG=${DOCKER_TAG:-$(git describe --tags --match 'v[0-9]*' --dirty='-dev')}

docker build -t ${DOCKER_ORG}/obra-hacks:${DOCKER_TAG} -t ${DOCKER_ORG}/obra-hacks:latest ../
