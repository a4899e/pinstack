# syntax=docker/dockerfile:1.19.0@sha256:a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4
# Built with: docker buildx build --build-context python-common-deps=<dir> .
FROM python:3.13-slim@sha256:a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4

WORKDIR /app

COPY --from=python-common-deps / /usr/local/lib/python3.13/site-packages/
RUN --mount=type=bind,from=python-common-deps,target=/deps ls /deps
COPY --from=other-deps / /opt/other/

FROM python-common-deps AS deps
