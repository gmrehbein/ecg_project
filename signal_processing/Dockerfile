FROM python:3.11-slim

# Environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/root

# Install system packages and Python libs
RUN apt-get update && apt-get install -y \
    build-essential \
    libnng-dev \
    socat \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    pynng \
    pyserial \
    msgpack \
    msgpack_numpy \
    scipy \
    numpy

# Copy simulator + processor sources
COPY ./src /root/src
# Set work directory
WORKDIR /root/src


CMD ["python3", "signal_processing.py"]

