"""
ECG Logger Module with Manifest Indexing

This logger captures ECG signal data streamed on a topic (e.g., b"ecg.raw" or b"ecg.filtered")
and stores it in a structured, partitioned directory layout with rotating Parquet files
and session metadata sidecars.

Each session is stored in:
    logs/<patient_id>/<session_id>/
    ├── patient_<id>_0000.parquet
    ├── patient_<id>_0001.parquet
    └── session.meta.json

A global manifest file at:
    logs/index.json
keeps track of all sessions across patients, enabling easy discovery, auditing,
and integration with analytics tools such as DuckDB.

Supported topics and lead sets:
    b"ecg.raw"      → ["RA", "LA", "LL"]
    b"ecg.filtered" → ["I", "II", "III", "aVR", "aVL", "aVF"]
"""

import os
import json
import uuid
import logging
import threading
import time
from datetime import datetime
from typing import List, Dict, Optional

import pandas as pd

from ecg_config.settings import ECG_DATABASE_DIR

from socket_stream import ecg_socket_stream

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def generate_patient_id() -> str:
    """Generate a synthetic patient ID using a UUID fragment."""
    return f"patient_{uuid.uuid4().hex[:8]}"


def generate_session_id() -> str:
    """Generate a session ID based on current UTC timestamp."""
    return datetime.utcnow().strftime("session_%Y%m%dT%H%M%S")


def write_metadata_json(
    patient_id: str,
    session_id: str,
    log_dir: str,
    leads: List[str],
    sampling_rate: int = 100,
    schema_version: str = "1.0.0"
) -> None:
    """Write session metadata to a JSON sidecar file."""
    meta = {
        "patient": patient_id,
        "session": session_id,
        "created": datetime.utcnow().isoformat(),
        "sampling_rate (hz)": sampling_rate,
        "leads": leads,
        "schema_version": schema_version
    }
    meta_path = os.path.join(log_dir, "session.meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)


def append_to_manifest(
    root_log_dir: str,
    patient_id: str,
    session_id: str,
    topic: str,
    file_count: int,
    timestamp: str
) -> None:
    """Append a session entry to the global manifest file (index.json)."""
    index_path = os.path.join(root_log_dir, "index.json")
    entry = {
        "patient": patient_id,
        "session": session_id,
        "topic": topic,
        "files": file_count,
        "started": timestamp,
        "path": os.path.join(root_log_dir, patient_id, session_id)
    }

    try:
        if os.path.exists(index_path):
            with open(index_path, "r") as f:
                index = json.load(f)
        else:
            index = []

        index.append(entry)

        with open(index_path, "w") as f:
            json.dump(index, f, indent=2)

        logger.info("Appended session to index manifest: %s", index_path)
    except Exception as e:
        logger.error("Failed to update manifest index: %s", e)


class ECGLogger:
    def __init__(
        self,
        topic: bytes = b"ecg.raw",
        root_log_dir: str = ECG_DATABASE_DIR,
        batch_size: int = 6000,
        patient_id: Optional[str] = None
    ) -> None:
        self.running: bool = False
        self.topic: bytes = topic
        self.batch_size: int = batch_size
        self.root_log_dir = root_log_dir

        self.patient_id: str = patient_id if patient_id else generate_patient_id()
        self.session_id: str = generate_session_id()
        self.session_timestamp: str = datetime.utcnow().isoformat()

        self.log_dir: str = os.path.join(root_log_dir, self.patient_id, self.session_id)
        os.makedirs(self.log_dir, exist_ok=True)

        self._file_index: int = 0

        self.leads = {
            b"ecg.raw": ["RA", "LA", "LL"],
            b"ecg.filtered": ["I", "II", "III", "aVR", "aVL", "aVF"]
        }.get(self.topic, ["unknown"])

        write_metadata_json(
            self.patient_id,
            self.session_id,
            self.log_dir,
            leads=self.leads
        )

        self._buffer: List[Dict] = []   # accumulated ECG data not yet flushed to disk


    def start(self, stop_event: Optional[threading.Event] = None, timeout: Optional[float] = None) -> None:
        """Start the logger and begin logging in the current thread."""
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

    def stop(self, stop_event: Optional[threading.Event] = None) -> None:
        """Stop the logger, flush buffer, signal stop event, and record a manifest entry."""
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
            file_count=self._file_index,
            timestamp=self.session_timestamp
        )

    def _log_data_loop(self, stop_event: Optional[threading.Event] = None) -> None:
        """Background loop that receives ECG data and buffers it for writing."""
        logger.info("Starting socket listener on %s", self.topic.decode())
        buffer: List[Dict] = []

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

            except Exception:
                logger.exception("Error while processing ECG data")

    def _write_batch(self, batch: List[Dict]) -> None:
        """Write a batch of ECG records to a Parquet file."""
        df: pd.DataFrame = pd.DataFrame.from_records(batch)
        filename = os.path.join(
            self.log_dir,
            f"{self.patient_id}_{self._file_index:04d}.parquet"
        )

        df.to_parquet(
            filename,
            compression="snappy",
            engine="pyarrow"
        )

        logger.info("Wrote %d samples to parquet file: %s", len(df), filename)
        self._file_index += 1
