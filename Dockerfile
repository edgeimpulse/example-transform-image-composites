# Pin the Debian release: python:3.11.0-slim is based on Debian 11 (bullseye),
# whose security repo has been moved to archive.debian.org (apt gets 404s).
FROM python:3.11-slim-bookworm

WORKDIR /app

# install libmagickwand-dev
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagickwand-dev \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt ./
RUN pip3 --no-cache-dir install -r requirements.txt

COPY . ./

ENTRYPOINT [ "python3", "-u", "transform.py" ]