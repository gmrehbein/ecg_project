FROM python:3.11-slim

# Set environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/root

# Set working directory
WORKDIR /root/src

# Install system dependencies needed to build some pip packages
RUN apt-get update && apt-get install -y \
    build-essential \
    libffi-dev \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir \
    flask \
    flask-cors \
    pynng \
    msgpack \
    msgpack_numpy \
    pandas \
    pyarrow

# Copy app source
COPY src/ /root/src/

# Optional: make app.py executable
RUN chmod +x app.py

# Expose Flask port
EXPOSE 5000

# Run the app
CMD ["python3", "app.py"]

