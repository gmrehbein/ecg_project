#!/usr/bin/env python3
"""
ECG Simulator (Virtual Serial Stream, Cross-Platform)

Simulates a synthetic 3-electrode ECG signal (RA, LA, LL) and streams it
over a virtual serial port. Prefers creating full virtual serial ports with `socat`
if available, otherwise falls back to a basic PTY pair using `pty.openpty()`.

Main entry point: `python ecg_simulator.py`
"""

import argparse
import json
import logging
import os
import pty
import random
import shutil
import subprocess
import sys
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
    Generate a synthetic 3-electrode ECG signal with optional noise artifacts.

    Parameters:
        fs (int): Sampling rate in Hz.
        duration (int): Length of the signal in seconds.
        loop (bool): If True, loop the signal endlessly.
        powerline_freq (float): Frequency of the simulated powerline interference (Hz).
        powerline_amplitude (float): Amplitude of powerline noise (mV).
        motion_amplitude (float): Amplitude of motion artifacts (mV).
        motion_interval (int): Interval in seconds between motion bursts.

    Yields:
        dict: A dictionary with keys 'RA', 'LA', 'LL' representing one ECG sample.
    """
    n_samples = int(fs * duration)
    t = np.arange(n_samples) / fs

    # Simulate Lead II ECG
    lead_ii = nk.ecg_simulate(
        duration=duration, sampling_rate=fs, random_state=random.randint(0, 1_000_000)
    )
    lead_ii *= 1.2 / (np.max(lead_ii) - np.min(lead_ii))  # Normalize

    # Generate RA, LA, LL leads
    RA = np.zeros_like(lead_ii)
    LL = RA + lead_ii
    LA = RA + 0.5 * (LL - RA) - 0.1 * np.std(LL)

    # Inject powerline noise
    powerline_noise = powerline_amplitude * np.sin(2 * np.pi * powerline_freq * t)
    RA += powerline_noise
    LA += powerline_noise
    LL += powerline_noise

    # Inject motion artifacts
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
    Create a virtual serial device using `socat` if available,
    otherwise fall back to `pty.openpty()`.

    Returns:
        tuple: (writer_path_or_fd, reader_path)
            - If using socat: (path_to_writer_device, path_to_reader_device)
            - If falling back: (master_fd (int), slave_path (str))
    """
    if shutil.which("socat") is not None:
        logging.info("Using socat to create virtual serial ports...")

        cmd = [
            "socat",
            "-d", "-d",
            "pty,raw,echo=0",
            "pty,raw,echo=0",
        ]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # Parse PTY device paths from socat output
        slave1, slave2 = None, None
        for _ in range(10):
            line = proc.stderr.readline()
            if "PTY is" in line:
                if slave1 is None:
                    slave1 = line.split("PTY is")[1].strip()
                else:
                    slave2 = line.split("PTY is")[1].strip()
                    break

        if not slave1 or not slave2:
            raise RuntimeError("Failed to create virtual serial ports with socat.")

        logging.info(f"Created virtual serial ports: {slave1} <-> {slave2}")
        return slave1, slave2

    else:
        logging.warning("socat not found, falling back to pty.openpty()")
        master_fd, slave_fd = pty.openpty()
        slave_name = os.ttyname(slave_fd)
        return master_fd, slave_name


def create_symlink_to_device(slave_path):
    """
    Create or replace a symlink pointing to the virtual serial device.

    Parameters:
        slave_path (str): The path of the slave end of the virtual PTY or socat device.
    """
    symlink_path = Path(SERIAL_DEVICE_PATH)
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()
    symlink_path.symlink_to(Path(slave_path))
    logging.info("Symlink created: %s -> %s", symlink_path, slave_path)


def stream_simulated_ecg_to_serial(writer, fs, duration, loop, noise, motion):
    """
    Continuously write JSON-encoded ECG samples to the master side of the virtual serial device.

    Parameters:
        writer (int or str): File descriptor (pty) or device path to write to.
        fs (int): Sampling rate in Hz.
        duration (int): Duration of the generated signal in seconds.
        loop (bool): Whether to loop the ECG signal endlessly.
        noise (float): Amplitude of simulated powerline interference.
        motion (float): Amplitude of simulated motion artifacts.
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
        if isinstance(writer, int):
            os.write(writer, line.encode("utf-8"))
        else:
            with open(writer, "wb", buffering=0) as f:
                f.write(line.encode("utf-8"))


def main():
    """
    Command-line entry point for the ECG simulator.

    Parses arguments, sets up the virtual serial device, and starts streaming ECG data.
    """
    parser = argparse.ArgumentParser(
        description="Simulate ECG stream on a virtual serial port."
    )
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="Set the logging level")
    parser.add_argument("--fs", type=int, default=100, help="Sampling rate in Hz")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    parser.add_argument("--loop", action="store_true", default=True, help="Loop the signal endlessly")
    parser.add_argument("--noise", type=float, default=0.05, help="Powerline noise amplitude (mV)")
    parser.add_argument("--motion", type=float, default=0.1, help="Motion artifact amplitude (mV)")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="[%(levelname)s] %(message)s"
    )

    # Create virtual serial device (prefer socat, fallback to PTY)
    writer, reader = create_virtual_serial_port()
    create_symlink_to_device(reader)

    # Launch streaming thread
    thread = threading.Thread(
        target=stream_simulated_ecg_to_serial,
        args=(writer, args.fs, args.duration, args.loop, args.noise, args.motion),
        daemon=True,
    )
    thread.start()

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Stopping ECG simulation.")


if __name__ == "__main__":
    main()
