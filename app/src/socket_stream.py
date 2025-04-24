import logging
import threading
import time

import msgpack
import msgpack_numpy as m
import pynng

from ecg_config.settings import SIGNAL_PROCESSOR_ADDRESS

m.patch()

def ecg_socket_stream(
    topic: bytes,
    address: str = SIGNAL_PROCESSOR_ADDRESS,
    recv_timeout_ms: int | None = None,
    reconnect_delay: float = 2.0,
    stop_event: threading.Event | None = None,
):
    """
    Yields a stream of ECG samples from a given NNG Pub/Sub topic, with
    automatic reconnection on failure. By default, blocks indefinitely on recv().

    Parameters
    ----------
    topic : bytes
        The subscription topic (e.g., b'ecg.filtered', b'ecg.raw').
    address : str
        The address of the NNG publisher (default: Host address of the SignalProcessing node).
    recv_timeout_ms : int or None
        If specified, sets a timeout (in milliseconds) for recv().
        If None (default), recv() blocks indefinitely.
    reconnect_delay : float
        Seconds to wait before retrying connection after a failure.
    stop_event : threading.Event or None
        Optional threading event to allow graceful exit of the socket loop.

    Yields
    ------
    dict
        Deserialized msgpack payload (automatically decodes NumPy arrays).
    """
    while True:
        if stop_event and stop_event.is_set():
            logging.info("Socket stream stop event set before connection attempt.")
            break
        try:
            kwargs = {}
            if recv_timeout_ms is not None:
                kwargs["recv_timeout"] = recv_timeout_ms

            with pynng.Sub0(**kwargs) as sub:
                logging.info("Subscribing to topic: %s", topic)
                sub.subscribe(topic)

                logging.info("Dialing address: %s", address)
                sub.dial(address)
                logging.info("Connected to %s on topic '%s'", address, topic.decode())

                while True:
                    if stop_event and stop_event.is_set():
                        logging.info("Socket stream stop event received, exiting inner loop.")
                        return
                    try:
                        msg = sub.recv()
                        payload = msg[len(topic):]
                        data = msgpack.unpackb(payload, object_hook=m.decode)
                        yield data

                    except pynng.Closed:
                        logging.warning("Socket closed on topic '%s'", topic.decode())
                        break  # triggers outer reconnect loop

        except Exception as e:
            logging.error("Connection failed to %s on topic '%s': %s", address, topic.decode(), e, exc_info=True)
            if stop_event and stop_event.is_set():
                logging.info("Socket stream stop event received during failure handling, exiting.")
                break
            logging.info("Retrying in %.1f seconds...", reconnect_delay)
            time.sleep(reconnect_delay)
