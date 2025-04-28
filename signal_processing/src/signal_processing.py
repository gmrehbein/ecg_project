#!/usr/bin/env python3
"""
ECG Signal Processor Pipeline
"""

import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass

import msgpack
import msgpack_numpy as m
import numpy as np
import pynng
import serial
from ecg_config.settings import SERIAL_DEVICE_PATH

import filters


@dataclass
class RawSample:
    """
    A triple of raw electrode voltages timestamped at the moment they were
    captured off the serial device
    """

    # a 1x3 numpy array of float32 values
    signal: np.ndarray
    # timestamp with microsecond precision
    timestamp: np.float64


# enable msgpack numpy support
m.patch()

# configure logging
logger = logging.getLogger(__name__)


def open_serial_device(path, baudrate=115200, timeout=1, retries=3, delay=1):
    """
    Attempts to open a serial device, resolving symlinks if necessary.
    Retries on failure with optional delay between attempts.

    Parameters:
        path (str): Path to the serial device (can be a symlink)
        baudrate (int): Serial baudrate (default: 115200)
        timeout (float): Serial timeout in seconds (default: 1)
        retries (int): Number of retry attempts if opening fails (default: 3)
        delay (float): Delay in seconds between retries (default: 1)

    Returns:
        serial.Serial: Opened serial connection object

    Raises:
        serial.SerialException: If all attempts fail
    """
    real_path = os.path.realpath(path)

    for attempt in range(1, retries + 1):
        try:
            logging.info("Attempt %d to open serial port: %s", attempt, path)
            return serial.Serial(path, baudrate=baudrate, timeout=timeout)
        except serial.SerialException as e1:
            logging.warning("Failed to open %s: %s", path, e1)
            if real_path != path:
                try:
                    logging.info("Retrying with resolved path: %s", real_path)
                    return serial.Serial(real_path, baudrate=baudrate, timeout=timeout)
                except serial.SerialException as e2:
                    logging.error("Failed to open resolved path %s: %s", real_path, e2)
            if attempt < retries:
                logging.info("Retrying in %d seconds...", delay)
                time.sleep(delay)

        logging.error("All %d attempts failed to open serial device.", retries)
        raise serial.SerialException(
            f"Failed to open serial device after {retries} attempts"
        )


def ecg_serial_stream():
    """
    Yields a stream of raw, unfiltered ECG voltages (RA,LA,LL) captured from a serial device
    Data is timestamped upon receipt
    Returns:
        A RawSample object containing a timestamped np.ndarray of voltages
    """
    # Setup serial input device
    logger.debug("attempting to open %s", SERIAL_DEVICE_PATH)
    with open_serial_device(SERIAL_DEVICE_PATH) as serial_device:
        serial_device.readline()  # discard likely incomplete first line
        while True:
            try:
                line = serial_device.readline()
                if not line:
                    continue  # skip upon timeout or empty lines

                data = json.loads(line.decode("utf-8").strip())
                signal = np.array(
                    [data["RA"], data["LA"], data["LL"]], dtype=np.float32
                )
                timestamp = np.float64(time.time_ns() / 1e9)
                yield RawSample(signal=signal, timestamp=timestamp)

            except (UnicodeDecodeError, json.decoder.JSONDecodeError) as e:
                logging.warning("ECG decode/parsing error: %s", e)
                continue


def serial_reader(out_queue):
    """
    main function for the serial reader thread
    """
    for sample in ecg_serial_stream():
        if sample is not None:
            out_queue.put(sample)


def serialize_filtered_leads(
    timestamp: np.float64, derived_leads: dict, bpm: float | None = None
) -> bytes:
    """
    Serialize filtered ECG signal data into a msgpack-encoded binary format.

    Parameters
    ----------
    timestamp : np.float64
        The UNIX timestamp (in seconds) associated with the filtered ECG sample.
    derived_leads : dict
        Dictionary of derived ECG leads (e.g., {"II": 0.42, "aVF": -0.12}).
        All values should be convertible to float.
    bpm : float, optional
        The beats-per-minute estimate, if available.

    Returns
    -------
    bytes
        A msgpack-encoded binary representation of the sample,
        including timestamp, lead voltages, and optionally BPM.

    Notes
    -----
    The output dictionary structure is:
    {
        "timestamp": float,
        "leads": {str: float, ...},
        "bpm": float (optional)
    }
    """

    payload = {
        "timestamp": float(timestamp),
        "leads": {k: float(v) for k, v in derived_leads.items()},
    }

    if bpm is not None:
        payload["bpm"] = float(bpm)

    return msgpack.packb(payload, default=m.encode, use_bin_type=True)


def serialize_raw_sample(timestamp: np.float64, voltages: np.ndarray) -> bytes:
    """
    Serializes a timestamp and the raw ECG electrode voltages (RA, LA, LL)
    into a msgpack-encoded binary blob for socket transmission.

    Args:
        timestamp (np.float64): UNIX timestamp in seconds.
        voltages (np.ndarray): Array of shape (3,) representing [RA, LA, LL] voltages.

    Returns:
        bytes: Msgpack-encoded binary payload with named fields.
    """
    payload = {
        "timestamp": float(timestamp),
        "RA": float(voltages[0]),
        "LA": float(voltages[1]),
        "LL": float(voltages[2]),
    }

    return msgpack.packb(payload, default=m.encode, use_bin_type=True)


# pylint: disable=too-few-public-methods
class SignalProcessor:
    """
    ECG Signal Processor class
    """

    def __init__(self):

        # Create inter-thread queue for relaying raw ECG samples
        self._raw_sample_queue = queue.Queue()

        # Create the thread to capture raw ECG samples off the serial device
        self._capture_thread = threading.Thread(
            target=serial_reader,
            args=(self._raw_sample_queue,),
            daemon=True,
        )

        # Create the publishing socket
        self._pub = pynng.Pub0()

    def run(self):
        """
        Runs the ecg signal processor pipeline
        """
        # Create the RT Filter Engine
        filter_engine = filters.FilterEngine()
        hr_monitor = filters.SingleChannelHRMonitor()

        # Open the publishing socket on port 9999
        # N.B. We don't use the ecg_config.settings.SIGNAL_PROCESSOR_ADDRESS here as
        # it's proven problematic when running in Docker. Just bind to all addresses
        address = "tcp://0.0.0.0:9999"
        logging.debug("opening pub socket on %s", address)
        self._pub.listen(address)

        # Launch the raw capture thread
        self._capture_thread.start()

        # main processing loop
        while True:
            # collect the raw voltage triple from the serial device
            raw_sample = self._raw_sample_queue.get()  # blocking call

            # filter out power-line interference, DC offset, and EMG noise
            filtered_signal = filter_engine.filter(raw_sample.signal)
            timestamp = raw_sample.timestamp

            # calculate the 6 derived leads from the filtered signals
            leads = filters.compute_leads_from_sample(filtered_signal)

            # append filtered lead II sample for single-line BPM estimation
            # readings will be available after the first 1s has elasped
            hr_monitor.append(leads["II"], timestamp)
            bpm = hr_monitor.process_latest_window()

            # serialize the data into a msgpack for transmission over the IPC channel
            msg_filtered = serialize_filtered_leads(timestamp, leads, bpm)

            # serialize the raw sample for transmission over the IPC channel
            msg_raw = serialize_raw_sample(timestamp, raw_sample.signal)

            # publish raw sample to the ecg.raw topic
            self._pub.send(b"ecg.raw" + msg_raw)

            # publish filtered sample to the ecg.filtered topic
            self._pub.send(b"ecg.filtered" + msg_filtered)


def main():
    """
    Entry point for the ECG signal processing pipeline:
    """
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
        ],
    )

    logger.info("Starting ECG Signal Processor")
    logger.debug("Logging to file: ecg_processor.log")
    # Create ECG processor instance
    signal_processor = SignalProcessor()

    # Start the ECG processor pipeline
    signal_processor.run()


if __name__ == "__main__":
    main()
