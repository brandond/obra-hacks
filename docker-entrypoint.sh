#!/bin/bash
set -eo pipefail
shopt -s nullglob

source /app/venv/bin/activate

if [ "$1" == "uwsgi" ]; then
  if [ -z "${ADMIN_USER}" ]; then
    ADMIN_USER='admin'
    echo "Default ADMIN_USER: ${ADMIN_USER}"
  fi

  if [ -z "${ADMIN_PASS}" ]; then
    ADMIN_SECRET=`openssl rand -base64 33 | tr -d '=' | tr '/+' '_-'`
    echo "Random ADMIN_PASS: ${ADMIN_PASS}"
  fi

  if [ -f "${UWSGI_KEY}" -a -f "${UWSGI_CERT}" ]; then
    echo "Skipping cert generation"
  else
    umask 77
    openssl req -new -newkey rsa:2048 -days 3650 -nodes -x509 -keyout ${UWSGI_KEY} -out ${UWSGI_CERT} -subj "/O=container/OU=uwsgi/CN=$HOSTNAME"
  fi
fi

exec "$@"
