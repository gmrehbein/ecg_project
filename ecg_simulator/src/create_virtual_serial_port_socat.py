import logging
import os
import platform
import subprocess
import time
from pathlib import Path

from ecg_config.settings import SERIAL_DEVICE_PATH


def create_virtual_serial_port_socat():
    """
    On macOS, use socat to create a pair of linked PTY devices to simulate a serial port.
    Returns the path to the PTY that should be used by the simulator for writing.
    """
    if platform.system() != "Darwin":
        raise EnvironmentError("This function is only intended for macOS (Darwin).")

    # Start socat process in the background
    logging.info("Starting socat to create virtual serial ports...")
    socat_cmd = ["socat", "-d", "-d", "pty,raw,echo=0", "pty,raw,echo=0"]
    proc = subprocess.Popen(
        socat_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    pty_paths = []
    start_time = time.time()
    for line in proc.stdout:
        logging.debug("socat: %s", line.strip())
        if "/dev/ttys" in line:
            path = line.strip().split()[-1]
            if os.path.exists(path):
                pty_paths.append(path)
        if len(pty_paths) == 2:
            break
        if time.time() - start_time > 5:
            proc.kill()
            raise TimeoutError("Timed out waiting for socat to create PTYs")

    if len(pty_paths) != 2:
        raise RuntimeError("Failed to extract both PTY paths from socat output")

    writer_path = pty_paths[0]
    reader_path = pty_paths[1]

    logging.info("socat PTY pair created: writer=%s, reader=%s", writer_path, reader_path)

    # Create or replace symlink
    symlink_path = Path(SERIAL_DEVICE_PATH)
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()
    symlink_path.symlink_to(Path(reader_path))
    logging.info("Symlink created: %s -> %s", symlink_path, reader_path)

    return writer_path  # return the end your simulator should write to
