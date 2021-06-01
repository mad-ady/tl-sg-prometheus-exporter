# Build a virtualenv using the appropriate Debian release
# * Install python3-venv for the built-in Python3 venv module (not installed by default)
# * Install gcc libpython3-dev to compile C Python modules
# * Update pip to support bdist_wheel
FROM docker.io/debian:buster-slim AS build
RUN apt-get update && \
    apt-get install --no-install-suggests --no-install-recommends --yes wget python3-venv gcc libpython3-dev && \
    python3 -m venv /venv && \
    /venv/bin/pip install --upgrade pip

# Install dumb-init in the build stage as the distroless image doesn't have the tools
# to obtain it
RUN /venv/bin/pip install dumb-init


# Build the virtualenv as a separate step: Only re-execute this step when requirements.txt changes
FROM build AS build-venv
COPY requirements.txt /requirements.txt
RUN /venv/bin/pip install --disable-pip-version-check -r /requirements.txt


# Copy the virtualenv into a distroless image
FROM gcr.io/distroless/python3-debian10

COPY --from=build /venv/bin/dumb-init /usr/bin/dumb-init
ENTRYPOINT ["/usr/bin/dumb-init", "--"]

WORKDIR /app

COPY --from=build-venv /venv /venv
COPY tl-sg-prometheus-exporter.py /app/exporter.py

CMD ["/venv/bin/python3", "exporter.py", "--config" , "/app/config.yaml"]
