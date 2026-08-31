FROM registry.access.redhat.com/ubi9/python-312@sha256:aebe03384391689993c42998836597e6161ac5340cbc84518c1b0528a1c59ea8

USER 0
WORKDIR /opt/app-root/src
COPY . .
RUN python -m pip install --no-cache-dir --no-compile . \
    && python -m pip check \
    && gcl-oss --help >/dev/null

ARG VCS_REF=unknown
LABEL org.opencontainers.image.source="https://github.com/jkershawrh/gcl-oss" \
      org.opencontainers.image.revision="$VCS_REF" \
      org.opencontainers.image.version="0.3.0a1" \
      org.opencontainers.image.licenses="Apache-2.0"

USER 1001
ENTRYPOINT ["gcl-oss"]
CMD ["--help"]
