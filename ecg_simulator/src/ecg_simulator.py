#!/usr/bin/env python3
"""
ECG Simulator (Virtual Serial Stream)

This module simulates a synthetic 3-electrode ECG signal (RA, LA, LL) and streams
it over a virtual serial port. It models realistic cardiac activity using NeuroKit2
and adds configurable noise and motion artifacts.

The virtual serial device can be symlinked into a known location (e.g., /root/dev/device)
for downstream signal processing tools to read as if from real hardware.

Main entry point: `python ecg_simulator_serial.py`
"""

import argparse
import json
import logging
import os
import pty
import random
import threading
import time
from pathlib import Path

import neurokit2 as nk
import numpy as np

from ecg_config.settings import SERIAL_DEVICE_PATH


def simulate_ecg_stream_noisy(
    fs=100,
    duration=60,
    loop=True,
    powerline_freq=60.0,
    powerline_amplitude=0.05,
    motion_amplitude=0.1,
    motion_interval=3,
):
    """
    Generate a synthetic 3-electrode ECG signal with optional powerline and motion noise.

    Parameters:
        fs (int): Sampling rate in Hz.
        duration (int): Length of the signal in seconds.
        loop (bool): If True, loop the signal endlessly.
        powerline_freq (float): Frequency of the simulated powerline interference.
        powerline_amplitude (float): Amplitude of powerline noise in mV.
        motion_amplitude (float): Amplitude of motion artifacts in mV.
        motion_interval (int): Interval in seconds between simulated motion events.

    Yields:
        dict: A dictionary representing one ECG sample with keys: 'RA', 'LA', 'LL'.
    """
    n_samples = int(fs * duration)
    t = np.arange(n_samples) / fs

    # Generate a synthetic 3-electrode ECG signal with optional randomized noise and motion.
    # Randomize parameters if not explicitly set
    lead_ii = nk.ecg_simulate(
        duration=duration, sampling_rate=fs, random_state=random.randint(0, 1_000_000)
    )
    lead_ii *= 1.2 / (np.max(lead_ii) - np.min(lead_ii))

    RA = np.zeros_like(lead_ii)
    LL = RA + lead_ii
    LA = RA + 0.5 * (LL - RA) - 0.1 * np.std(LL)

    powerline_noise = powerline_amplitude * np.sin(2 * np.pi * powerline_freq * t)
    RA += powerline_noise
    LA += powerline_noise
    LL += powerline_noise

    motion_signal = np.zeros_like(t)
    motion_sample_interval = int(motion_interval * fs)
    for j in range(0, len(t), motion_sample_interval):
        if j + 10 < len(t):
            motion_signal[j : j + 10] += np.linspace(0, motion_amplitude, 10)

    RA += motion_signal
    LA += 0.5 * motion_signal
    LL += 0.8 * motion_signal

    sample_period = 1.0 / fs

    i = 0
    while True:
        yield {"RA": float(RA[i]), "LA": float(LA[i]), "LL": float(LL[i])}
        time.sleep(sample_period)
        i += 1
        if i >= len(RA):
            if loop:
                i = 0
            else:
                break


def create_virtual_serial_port():
    """
    Create a new virtual PTY pair to simulate a serial device.

    Returns:
        tuple: (master_fd, slave_name) where master_fd is the file descriptor
               for writing, and slave_name is the path to the simulated device.
    """
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)
    return master_fd, slave_name


def create_symlink_to_device(slave_path):
    """
    Create or replace a symlink pointing to the virtual serial device.

    Parameters:
        slave_path (str): The path of the slave end of the virtual PTY.
    """
    symlink_path = Path(SERIAL_DEVICE_PATH)
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()
    symlink_path.symlink_to(Path(slave_path))
    logging.info("Symlink created: %s -> %s", symlink_path, slave_path)


def stream_simulated_ecg_to_serial(master_fd, fs, duration, loop, noise, motion):
    """
    Continuously write JSON-encoded ECG samples to the master side of the virtual serial port.

    Parameters:
        master_fd (int): File descriptor for the PTY master (writer).
        fs (int): Sampling rate in Hz.
        duration (int): Duration of the generated signal in seconds.
        loop (bool): Whether to loop the ECG signal.
        noise (float): Amplitude of powerline interference in mV.
        motion (float): Amplitude of motion artifacts in mV.
    """
    generator = simulate_ecg_stream_noisy(
        fs=fs,
        duration=duration,
        loop=loop,
        powerline_amplitude=noise,
        motion_amplitude=motion,
    )
    for sample in generator:
        line = json.dumps(sample) + "\n"
        os.write(master_fd, line.encode("utf-8"))


def main():
    """
    Command-line entry point for the ECG simulator. Creates a virtual serial port and
    streams ECG data with optional noise and motion artifacts.
    """
    parser = argparse.ArgumentParser(
        description="Simulate ECG stream on a virtual serial port."
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level",
    )
    parser.add_argument("--fs", type=int, default=100, help="Sampling rate in Hz")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    parser.add_argument(
        "--loop", action="store_true", default=True, help="Loop the signal endlessly"
    )
    parser.add_argument(
        "--noise", type=float, default=0.05, help="Powerline noise amplitude (mV)"
    )
    parser.add_argument(
        "--motion", type=float, default=0.1, help="Motion artifact amplitude (mV)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level), format="[%(levelname)s] %(message)s"
    )

    master_fd, slave_name = create_virtual_serial_port()
    logging.info("Virtual serial device created: %s", slave_name)

    create_symlink_to_device(slave_name)
    logging.info("Use ~/device in your ECG signal processor.")

    thread = threading.Thread(
        target=stream_simulated_ecg_to_serial,
        args=(master_fd, args.fs, args.duration, args.loop, args.noise, args.motion),
        daemon=True,
    )
    thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Stopping ECG simulation.")


if __name__ == "__main__":
    main()
