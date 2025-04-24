#!/usr/bin/env python3
"""
Test harness for ECGLogger.

This script launches the ECGLogger in a background thread, captures ECG data
from the 'ecg.raw' topic, and writes it to rotating Parquet files. It supports
graceful shutdown on SIGINT or SIGTERM, and optional timeout via CLI.
"""

import signal
import logging
import threading
import argparse
from ecg_logger import ECGLogger

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Parse CLI arguments
parser = argparse.ArgumentParser(description="Test ECGLogger standalone.")
parser.add_argument("--timeout", type=int, default=None, help="Stop after this many seconds")
args = parser.parse_args()

# Create stop event shared between thread and signal handler
stop_event = threading.Event()

# Instantiate and start logger in its own thread
logger_instance = ECGLogger()
logger_thread = threading.Thread(
    target=logger_instance.start,
    kwargs={"stop_event": stop_event, "timeout": args.timeout},
    daemon=True
)
logger_thread.start()

# Signal handler for graceful shutdown
def shutdown_handler(signum, frame):
    logger.info("Signal %s received. Stopping logger...", signum)
    logger_instance.stop(stop_event)
    logger_thread.join(timeout=2.0)
    logger.info("Logger shutdown complete.")
    exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

#Keep the main thread alive until logger completes or signal is received
try:
    while logger_thread.is_alive():
        logger_thread.join(timeout=1)
except KeyboardInterrupt:
    shutdown_handler(signal.SIGINT, None)
