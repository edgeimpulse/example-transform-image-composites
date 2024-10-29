FROM python:3.11.0-slim

WORKDIR /app

# Python dependencies
COPY requirements.txt ./
RUN pip3 --no-cache-dir install -r requirements.txt

# install libmagickwand-dev
RUN apt-get update && apt-get install -y libmagickwand-dev

COPY . ./

ENTRYPOINT [ "python3", "-u", "transform.py" ]