"""
Shared configuration module for ECG signal processing and visualization system.

Loads:
    - the path to the serial device set-up by the ecg/hardware simulator
    - the path to the persistent parquet file logs
    - the address of the singal processor pub-sub socket

"""

import os
from pathlib import Path

# Path to serial ECG device (used by signal processor)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERIAL_DEVICE_PATH = str(PROJECT_ROOT / "dev" / "device")

# Directory for parquet ecg logs (used by ecg logger)
ECG_DATABASE_DIR = str(PROJECT_ROOT / "ecg_database")

# Base signal processor socket address
# IP address will be set in the envirornment under docker, otherwise use localhost
# For use by clients of the signal_processor only, not the signal_processor itself
# as this proven unereliable when running under Docker
SIGNAL_PROCESSOR_ADDRESS = os.getenv(
        "SIGNAL_PROCESSOR_ADDRESS",
        "tcp://localhost:9999"
)
