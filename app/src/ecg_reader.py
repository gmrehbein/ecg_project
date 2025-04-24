"""
ECGReader Module

Handles background subscription to filtered ECG data over a NNG socket,
buffering incoming samples for real-time access (e.g., by a Flask API).
"""

import logging
import queue
import threading
from typing import Optional

from ecg_config.settings import SIGNAL_PROCESSOR_ADDRESS
from socket_stream import ecg_socket_stream

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

class ECGReader:
    """
    Background ECG processor that subscribes to filtered ECG data over
    a NNG socket and buffers it for real-time access.
    """

    def __init__(
        self,
        address: str = SIGNAL_PROCESSOR_ADDRESS,
    ) -> None:

        self.running: bool = False

        # Peer address and port to connect to the signal processor socket
        self.address: str = address

        # Set the topic to subscribe to filtered ECG data published by the signal processor
        self.topic = b"ecg.filtered"

        # Inter-thread queue for relaying filtered ECG samples
        self._input_queue = queue.Queue()

    def get_current_data(self) -> dict:
        """
        Get the most recent ECG sample from the buffer.

        Returns:
            dict: The latest ECG sample, or empty dict if none available.
        """
        if not self._input_queue.empty():
            return self._input_queue.get()
        return {}

    def start(self, stop_event: Optional[threading.Event] = None) -> None:
        """
        Start the acquisition thread to consume filtered ECG data
        from the pub-sub socket.
        """
        self.running = True
        logger.info("ECGReader is running")
        self._read_data_loop(stop_event)

    def stop(self, stop_event: Optional[threading.Event] = None) -> None:
        """
        Stop the acquisition thread and wait briefly for it to shut down.
        """
        self.running = False
        if stop_event:
            stop_event.set()

        logger.info("Stopping ECGReader...")

    def _read_data_loop(self, stop_event: Optional[threading.Event] = None):
        """
        Internal thread loop to receive filtered ECG data from the 'ecg.filtered' topic
        and push it into a local buffer.
        """
        for msg in ecg_socket_stream(self.topic, address=self.address):
            if not self.running or (stop_event and stop_event.is_set()):
                logger.info("Reader shutting down.")
                break
            self._input_queue.put(msg)
