# Pantry Chef in a container.
#
# Two things about this app shape the image:
#
#   * Your cookbooks stay on the host and are mounted read-only. They are never
#     copied into the image — a cookbook library is large, and it is yours.
#   * The index and the photo cache must outlive the container, so /data is a
#     volume. Recipe photos are read out of the books on demand, which means
#     the books must stay mounted for the photos to keep working.

FROM python:3.13-slim AS base

# PyMuPDF and the Kindle reader ship manylinux wheels, so no compiler is
# needed; these are just the runtime libraries the wheels link against.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PANTRY_CHEF_DB=/data/pantry-chef.db

WORKDIR /app

# The package is copied before it is installed. Installing first and copying
# afterwards would leave an incomplete copy in site-packages, since a
# non-editable install snapshots the source rather than linking to it.
COPY pyproject.toml ./
COPY pantry_chef ./pantry_chef
RUN pip install --no-cache-dir ".[all]"

# Run as a non-root user. /data is chowned so a rootless container started with
# --userns=keep-id can still write the database.
RUN useradd --create-home --uid 1000 chef \
 && mkdir -p /data /books \
 && chown -R chef:chef /data /app
USER chef

VOLUME ["/data"]
EXPOSE 8077

# 0.0.0.0 rather than the CLI's usual 127.0.0.1: inside a container the
# loopback address is only reachable from inside the container.
CMD ["pantry-chef", "serve", "--host", "0.0.0.0", "--port", "8077"]
