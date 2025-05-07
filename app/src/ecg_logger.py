"""
ECG Logger Module with Manifest Indexing

This logger captures ECG signal data streamed on a topic (e.g., b"ecg.raw" or b"ecg.filtered")
and stores it in a structured, partitioned directory layout with rotating Parquet files
and session metadata sidecars.

If logging both raw and filtered topics, each is stored in a subdirectory under the session.

Each session is stored in:
    logs/<patient_id>/<session_id>/
        ├── ecg.raw/
        │   ├── patient_<id>_0000.parquet ← ecg time series data
        │   ├── ...
        │   └── ecg.raw.session.meta.json ← metadata for the raw topic capture
        ├── ecg.filtered/
        │   ├── patient_<id>_0000.parquet ← ecg time series data
        │   ├── ...
        │   └── ecg.filtered.session.meta.json  ← metadata for the filtered topic capture
        └── session.meta.json   ← summary metadata for the whole session

A global manifest file at:
    logs/index.json
keeps track of all sessions across patients, enabling easy discovery, auditing,
and integration with analytics tools such as DuckDB.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from filelock import FileLock
import pandas as pd
from socket_stream import ecg_socket_stream

from ecg_config.settings import ECG_DATABASE_DIR, HOST_GID, HOST_UID

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

SESSION_META_FILENAME = "session.meta.json"
INDEX_FILENAME = "index.json"
PARQUET_SUFFIX = ".parquet"

Topic = Literal[b"ecg.raw", b"ecg.filtered"]
"""Type alias for supported ECG socket topics used in ECGLogger."""

CHANNELS_FOR_TOPICS = {
    b"ecg.raw": ["RA", "LA", "LL"],
    b"ecg.filtered": ["I", "II", "III", "aVR", "aVL", "aVF"],
}


def chown_if_needed(path: str | Path) -> None:
    """Change ownership of the file or directory to host user/group if UID and GID are set."""
    try:
        if HOST_UID >= 0 and HOST_GID >= 0:
            os.chown(str(path), HOST_UID, HOST_GID)
    except OSError as e:
        logger.warning("Failed to chown %s: %s", path, e)


def generate_patient_id() -> str:
    """Generate a synthetic patient identifier using a short UUID prefix."""
    return f"patient_{uuid.uuid4().hex[:8]}"


def generate_session_id() -> str:
    """Generate a session ID using current UTC timestamp."""
    return datetime.now(timezone.utc).strftime("session_%Y%m%dT%H%M%S")


def generate_session_metadata(
    patient_id: str,
    session_id: str,
    channels: list[str],
    sampling_rate: int = 100,
    schema_version: str = "1.0.0",
) -> dict:
    """Generate session metadata dictionary for JSON sidecar."""
    return {
        "patient": patient_id,
        "session": session_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "sampling_rate (hz)": sampling_rate,
        "channels": channels,
        "schema_version": schema_version,
    }

def topic_meta_filename(topic: bytes) -> str:
    """Generate topic-specific metadata filename."""
    return f"{topic.decode()}.session.meta.json"


def write_metadata_json(log_dir: Path, metadata: dict, topic: bytes | None = None) -> None:
    """Write initial session metadata JSON to session directory."""
    filename = topic_meta_filename(topic) if topic else SESSION_META_FILENAME
    meta_path = log_dir / filename
    meta_path.write_text(json.dumps(metadata, indent=2))
    chown_if_needed(meta_path)


def finalize_topic_session_metadata(log_dir: Path, topic=bytes) -> None:
    """
    Closeout the topic.session metadata json file with first and last
    ECG timestamps and duration.
    """
    meta_path = log_dir /  topic_meta_filename(topic)
    try:
        with meta_path.open("r", encoding="utf-8") as f:
            meta = json.load(f)

        df_list = []
        for file in sorted(log_dir.glob("*.parquet")):
            try:
                df = pd.read_parquet(file, columns=["timestamp"])
                df_list.append(df)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("Failed to read %s: %s", file, e)

        if not df_list:
            logger.warning("No valid Parquet files found in %s", log_dir)
            return

        all_ts = pd.concat(df_list)["timestamp"].sort_values()
        first_ts = pd.to_datetime(all_ts.iloc[0], unit="s", utc=True)
        last_ts = pd.to_datetime(all_ts.iloc[-1], unit="s", utc=True)
        duration = (last_ts - first_ts).total_seconds()

        meta["first_sample_time"] = first_ts.isoformat()
        meta["last_sample_time"] = last_ts.isoformat()
        meta["duration_seconds"] = round(duration, 3)

        with meta_path.open("w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        chown_if_needed(meta_path)
        logger.info("Updated session.meta.json with end time and duration.")
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to update session.meta.json: %s", e)


def write_manifest_entries(root_db_dir: Path, entries: list[dict]) -> None:
    """Append a list of manifest entries to the global index.json manifest."""
    index_path = root_db_dir / INDEX_FILENAME
    lock_path = index_path.with_suffix(".lock")

    lock = FileLock(lock_path, timeout=10)  # Wait up to 10 seconds for the lock

    try:
        with lock:
            if index_path.exists():
                with index_path.open("r", encoding="utf-8") as f:
                    index = json.load(f)
            else:
                index = []

            index.extend(entries)

            with index_path.open("w", encoding="utf-8") as f:
                json.dump(index, f, indent=2)

            chown_if_needed(index_path)
            logger.info("Wrote %d entries to manifest index: %s", len(entries), index_path)

    except Timeout:
        logger.error("Timeout acquiring lock for index manifest: %s", lock_path)

def start_all_loggers(
    root_db_dir: str = ECG_DATABASE_DIR,
    batch_size: int = 6000,
    patient_id: str | None = None,
    stop_event: threading.Event | None = None,
    timeout: float | None = None,
) -> tuple[list[ECGLogger], list[threading.Thread]]:
    """Utility function to start one ECGLogger per supported topic (e.g., raw | filtered)
    in background threads."""
    patient_id = patient_id or generate_patient_id()
    session_id = generate_session_id()

    loggers = []
    threads = []

    for topic in CHANNELS_FOR_TOPICS:
        logger_instance = ECGLogger(
            topic=topic,
            session_id=session_id,
            root_db_dir=root_db_dir,
            batch_size=batch_size,
            patient_id=patient_id,
        )
        thread = threading.Thread(
            target=logger_instance.start,
            args=(stop_event, timeout),
            daemon=True,
        )
        thread.start()
        threads.append(thread)
        loggers.append(logger_instance)

    # Write root session-level metadata
    session_root_dir = Path(root_db_dir) / patient_id / session_id
    session_root_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "patient": patient_id,
        "session": session_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "topics": [topic.decode() for topic in CHANNELS_FOR_TOPICS],
        "schema_version": "1.0.0",
    }
    write_metadata_json(session_root_dir, metadata)

    return loggers, threads


def stop_all_loggers(
    loggers: list[ECGLogger],
    threads: list[threading.Thread],
    stop_event: threading.Event | None = None,
) -> None:
    """Stop all ECG loggers and write index manifest once all have completed."""
    entries = [logger.stop(stop_event) for logger in loggers]
    for thread in threads:
        thread.join()

    if entries:
        write_manifest_entries(Path(loggers[0].root_db_dir), entries)


class ECGLogger:
    """Logger class for streaming ECG data to Parquet with session metadata and indexing."""

    def __init__(
        self,
        topic: Topic,
        root_db_dir: str = ECG_DATABASE_DIR,
        batch_size: int = 6000,
        patient_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Initialize ECGLogger with topic, session ID, log directory, and metadata setup."""
        if topic not in CHANNELS_FOR_TOPICS:
            raise ValueError(f"Unrecognized topic: {topic!r}")

        self.running = False
        self.topic = topic
        self.batch_size = batch_size
        self.root_db_dir = Path(root_db_dir)

        self.patient_id = patient_id or generate_patient_id()
        self.session_id = session_id or generate_session_id()
        self.session_timestamp = datetime.now(timezone.utc).isoformat()

        topic_str = self.topic.decode()
        self.log_dir = self.root_db_dir / self.patient_id / self.session_id / topic_str
        self.log_dir.mkdir(parents=True, exist_ok=True)
        chown_if_needed(self.log_dir)

        self._file_index = 0
        self._files_written = 0
        self._buffer: list[dict] = []

        if self.topic not in CHANNELS_FOR_TOPICS:
            raise ValueError(f"Unrecognized topic: {self.topic!r}")

        self.channels = CHANNELS_FOR_TOPICS[self.topic]

        metadata = generate_session_metadata(
            self.patient_id, self.session_id, self.channels
        )
        write_metadata_json(self.log_dir, metadata, topic=self.topic)

    def _get_next_parquet_filename(self) -> Path:
        """Generate the next Parquet file path based on internal file counter."""
        return (
            self.log_dir / f"{self.patient_id}_{self._file_index:04d}{PARQUET_SUFFIX}"
        )

    def _write_batch(self, batch: list[dict]) -> None:
        """Write a batch of ECG samples to a compressed Parquet file."""
        df = pd.DataFrame.from_records(batch)
        filename = self._get_next_parquet_filename()
        df.to_parquet(filename, compression="snappy", engine="pyarrow")
        chown_if_needed(filename)
        self._files_written += 1
        logger.info("Wrote %d samples to: %s", len(df), filename)
        self._file_index += 1

    def _log_data_loop(self, stop_event: threading.Event | None = None) -> None:
        """
        Main processing loop.
        Continuously receive ECG data from the socket and write it in batches to parquet files.
        """
        logger.info("Listening on topic: %s", self.topic.decode())
        for data in ecg_socket_stream(topic=self.topic):
            if not self.running or (stop_event and stop_event.is_set()):
                break
            try:
                data["patient"] = self.patient_id
                data["session"] = self.session_id
                self._buffer.append(data)
                if len(self._buffer) >= self.batch_size:
                    self._write_batch(self._buffer)
                    self._buffer.clear()
            except (KeyError, TypeError, ValueError) as e:
                logger.exception("Malformed data received: %s", e)

    def start(
        self, stop_event: threading.Event | None = None, timeout: float | None = None
    ) -> None:
        """Begin logging session; optionally stop after timeout or on stop_event."""
        self.running = True
        logger.info("Logger started for %s/%s", self.patient_id, self.session_id)

        if timeout:

            def timeout_thread():
                logger.info("Timeout %.1fs started", timeout)
                time.sleep(timeout)
                self.stop(stop_event)

            threading.Thread(target=timeout_thread, daemon=True).start()

        self._log_data_loop(stop_event)

    def stop(self, stop_event: threading.Event | None = None) -> dict:
        """Begin logging session; optionally stop after timeout or on stop_event."""
        self.running = False
        if stop_event:
            stop_event.set()

        if self._buffer:
            self._write_batch(self._buffer)
            self._buffer.clear()

        logger.info("Stopping logger for topic %s", self.topic.decode())
        finalize_topic_session_metadata(self.log_dir, self.topic)

        started_dt = datetime.fromisoformat(self.session_timestamp)
        ended_dt = datetime.now(timezone.utc)
        duration = (ended_dt - started_dt).total_seconds()

        return {
            "patient": self.patient_id,
            "session": self.session_id,
            "topic": self.topic.decode(),
            "files": self._files_written,
            "start_time": self.session_timestamp,
            "stop_time": ended_dt.isoformat(),
            "duration_seconds": round(duration, 3),
            "path": str(self.log_dir.relative_to(self.root_db_dir)),
        }
