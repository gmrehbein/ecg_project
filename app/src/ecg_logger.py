"""
ECG Logger Module with Manifest Indexing

This logger captures ECG signal data streamed on a topic (e.g., b"ecg.raw" or b"ecg.filtered")
and stores it in a structured, partitioned directory layout with rotating Parquet files
and session metadata sidecars.

Each session is stored in:
    logs/<patient_id>/<session_id>/
    ├── patient_<id>_0000.parquet
    ├── patient_<id>_0001.parquet
    ├── ....
    ├── patient_<id>_N.parquet
    └── session.meta.json

A global manifest file at:
    logs/index.json
keeps track of all sessions across patients, enabling easy discovery, auditing,
and integration with analytics tools such as DuckDB.

Supported topics and lead sets:
    b"ecg.raw"      → ["timestamp", "RA", "LA", "LL"]
    b"ecg.filtered" → ["timestamp", "I", "II", "III", "aVR", "aVL", "aVF"]
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import pandas as pd
from ecg_config.settings import ECG_DATABASE_DIR, HOST_GID, HOST_UID

from socket_stream import ecg_socket_stream

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def chown_if_needed(path: str) -> None:
    """Change ownership of the given file or directory to HOST_UID:HOST_GID if set."""
    try:
        if HOST_UID >= 0 and HOST_GID >= 0:
            os.chown(path, HOST_UID, HOST_GID)
    except OSError as e:
        logger.warning("Failed to chown %s: %s", path, e)


def generate_patient_id() -> str:
    """Generate a synthetic patient identifier using a truncated UUID."""
    return f"patient_{uuid.uuid4().hex[:8]}"


def generate_session_id() -> str:
    """Generate a unique session identifier using the current UTC timestamp."""
    return datetime.now(timezone.utc).strftime("session_%Y%m%dT%H%M%S")


def write_metadata_json(
    patient_id: str,
    session_id: str,
    log_dir: str,
    channels: list[str],
    sampling_rate: int = 100,
    schema_version: str = "1.0.0",
) -> None:
    """Write session metadata including lead configuration, patient and session IDs to JSON."""
    meta = {
        "patient": patient_id,
        "session": session_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "sampling_rate (hz)": sampling_rate,
        "channels": channels,
        "schema_version": schema_version,
    }
    meta_path = os.path.join(log_dir, "session.meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    chown_if_needed(meta_path)


def append_to_manifest(
    root_log_dir: str,
    patient_id: str,
    session_id: str,
    topic: str,
    file_count: int,
    timestamp: str,
) -> None:
    """Append an entry for the completed session to a global manifest index in index.json."""
    logger.info("append_to_manifest called with root_log_dir=%s", root_log_dir)
    index_path = os.path.join(root_log_dir, "index.json")
    entry = {
        "patient": patient_id,
        "session": session_id,
        "topic": topic,
        "files": file_count,
        "started": timestamp,
        "path": os.path.join(root_log_dir, patient_id, session_id),
    }

    try:
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
        else:
            index = []

        index.append(entry)

        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)

        chown_if_needed(index_path)
        logger.info("Appended session to index manifest: %s", index_path)
    except Exception as e:
        logger.error("Failed to update manifest index: %s", e)


class ECGLogger:
    def __init__(
        self,
        topic: bytes = b"ecg.raw",
        root_log_dir: str = ECG_DATABASE_DIR,
        batch_size: int = 6000,
        patient_id: str | None = None,
    ) -> None:
        """Initialize ECGLogger with session metadata and prepare output directory."""
        self.running: bool = False
        self.topic: bytes = topic
        self.batch_size: int = batch_size
        self.root_log_dir = root_log_dir

        self.patient_id: str = patient_id if patient_id else generate_patient_id()
        self.session_id: str = generate_session_id()
        self.session_timestamp: str = datetime.now(timezone.utc).isoformat()

        self.log_dir: str = os.path.join(root_log_dir, self.patient_id, self.session_id)
        os.makedirs(self.log_dir, exist_ok=True)
        chown_if_needed(self.log_dir)

        self._file_index: int = 0  # for file naming
        self._files_written: int = 0  # for actual successful writes

        self.channels = {
            b"ecg.raw": ["RA", "LA", "LL"],
            b"ecg.filtered": ["I", "II", "III", "aVR", "aVL", "aVF"],
        }.get(self.topic, ["unknown"])

        write_metadata_json(
            self.patient_id, self.session_id, self.log_dir, channels=self.channels
        )

        self._buffer: list[dict] = []

    def start(
        self, stop_event: threading.Event | None = None, timeout: float | None = None
    ) -> None:
        """Start ECG data logging, optionally stop after a timeout or when stop_event is set."""
        self.running = True
        logger.info("ECGLogger is running for %s/%s", self.patient_id, self.session_id)

        if timeout:

            def timeout_thread():
                logger.info("Timeout of %.1f seconds started", timeout)
                time.sleep(timeout)
                logger.info("Timeout reached. Stopping logger.")
                self.stop(stop_event)

            threading.Thread(target=timeout_thread, daemon=True).start()

        self._log_data_loop(stop_event)

    def stop(self, stop_event: threading.Event | None = None) -> None:
        """Stop logging, flush any buffered data, and record session metadata to index."""
        logger.info("stop() called")

        self.running = False
        if stop_event:
            stop_event.set()

        if self._buffer:
            self._write_batch(self._buffer)
            self._buffer.clear()

        logger.info("Stopping ECGLogger...")

        append_to_manifest(
            self.root_log_dir,
            self.patient_id,
            self.session_id,
            topic=self.topic.decode(),
            file_count=self._files_written,
            timestamp=self.session_timestamp,
        )

    def _log_data_loop(self, stop_event: threading.Event | None = None) -> None:
        """Receive ECG data from the socket stream, buffer it, and write to Parquet files."""
        logger.info("Starting socket listener on %s", self.topic.decode())
        buffer: list[dict] = []

        for data in ecg_socket_stream(topic=self.topic):
            if not self.running or (stop_event and stop_event.is_set()):
                logger.info("Logger shutting down.")
                if buffer:
                    self._write_batch(buffer)
                    buffer.clear()
                break

            try:
                data["patient"] = self.patient_id
                data["session"] = self.session_id
                buffer.append(data)

                if len(buffer) >= self.batch_size:
                    self._write_batch(buffer)
                    buffer.clear()

            except (KeyError, TypeError, ValueError) as e:
                logger.exception("Error while processing ECG data: %s", e)

    def _write_batch(self, batch: list[dict]) -> None:
        """Write a batch of ECG records to a compressed Parquet file."""
        df = pd.DataFrame.from_records(batch)
        filename = os.path.join(
            self.log_dir, f"{self.patient_id}_{self._file_index:04d}.parquet"
        )

        df.to_parquet(filename, compression="snappy", engine="pyarrow")
        chown_if_needed(filename)
        self._files_written += 1  # Increment the number of files written
        logger.info("Wrote %d samples to parquet file: %s", len(df), filename)
        self._file_index += 1  # Increment the file index for the next write
