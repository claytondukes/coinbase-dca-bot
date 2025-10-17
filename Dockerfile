FROM python:3.11-slim

ENV TZ=Etc/UTC
ENV DEBIAN_FRONTEND=noninteractive

# Install tzdata non-interactively and clean apt lists
RUN apt-get update -yqq \
 && apt-get install -yqq --no-install-recommends tzdata \
 && ln -fs /usr/share/zoneinfo/$TZ /etc/localtime \
 && dpkg-reconfigure -f noninteractive tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app

# Leverage layer caching for dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the source
COPY . .
