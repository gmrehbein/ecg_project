FROM python:3.11-slim

# Install system dependencies and Python packages
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip3 install pyserial neurokit2 numpy

# Set Python path so /root is the base for `ecg_config.settings` imports
ENV PYTHONPATH=/root

COPY src /root/src
WORKDIR /root/src
RUN chmod +x ecg_simulator.py

CMD ["python3", "ecg_simulator.py"]
