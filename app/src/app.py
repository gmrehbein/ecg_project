#!/usr/bin/env python3
"""
ECG Visualization Web App

This Flask-based application provides real-time visualization of filtered ECG data
streamed from a signal processing backend over a NNG socket. It also starts an
ECGLogger in a background thread to record raw ECG data to Parquet.

Endpoints:
- GET `/`             → Renders the main ECG GUI
- GET `/api/data`     → Returns the latest ECG data as JSON
- GET `/api/stream`   → Streams ECG data via Server-Sent Events (SSE)
"""

import json
import logging
import signal
import sys
import threading
import time

import flask
import flask_cors

# local imports
from ecg_logger import start_all_loggers, stop_all_loggers
from ecg_reader import ECGReader

# Flask app
app = flask.Flask(__name__)
flask_cors.CORS(app)  # Enable CORS for all routes


def format_ecg_json(data: dict) -> dict:
    """
    Format an ECG data sample into a JSON-serializable dictionary.

    Parameters:
        data (dict): A raw ECG sample containing 'timestamp', 'leads', and optionally 'bpm'.

    Returns:
        dict: Formatted JSON-compatible payload.
    """
    if not data or "timestamp" not in data or "leads" not in data:
        return {}

    result = {"timestamp": data["timestamp"], "leads": data["leads"]}

    if "bpm" in data:
        result["bpm"] = data["bpm"]

    return result


@app.route("/")
def index():
    """
    Render the main ECG visualization HTML page.

    Returns:
        str: Rendered HTML page.
    """
    return flask.render_template("ecg.html")


@app.route("/api/data")
def get_data():
    """
    Return the latest ECG sample in JSON format.

    Returns:
        flask.Response: JSON-encoded ECG sample.
    """
    data = app.ecg_reader.get_current_data()
    return flask.jsonify(format_ecg_json(data))


@app.route("/api/stream")
def stream_data():
    """
    Stream ECG samples as Server-Sent Events (SSE) to connected clients.

    Returns:
        flask.Response: A streaming response with ECG data updates.
    """

    def generate():
        while True:
            data = app.ecg_reader.get_current_data()
            if not data:
                time.sleep(0.01)
                continue

            json_payload = format_ecg_json(data)
            if not json_payload:
                continue

            yield f"data: {json.dumps(json_payload)}\n\n"
            time.sleep(0.05)

    return flask.Response(generate(), mimetype="text/event-stream")


def main():
    """
    Main entrypoint for the ECG visualization server.
    """
    logging.basicConfig(level=logging.INFO)
    app.logger.info("Starting ECG Visualization")

    # --- Start ECG loggers (raw + filtered) ---
    app._ecg_logger_stop_event = threading.Event()
    app._ecg_logger_instances, app._ecg_logger_threads = start_all_loggers(
        stop_event=app._ecg_logger_stop_event
    )

    # --- Start ECG reader for GUI (filtered) ---
    app.ecg_reader = ECGReader()
    app._reader_stop_event = threading.Event()
    app._reader_thread = threading.Thread(
        target=app.ecg_reader.start,
        kwargs={"stop_event": app._reader_stop_event},
        daemon=True,
    )
    app._reader_thread.start()

    def shutdown(_signum, _frame):
        app.logger.info("Shutting down Flask app...")

        # Stop reader
        app.ecg_reader.stop(app._reader_stop_event)
        app._reader_thread.join(timeout=2.0)

        # Stop all loggers and join their threads
        stop_all_loggers(
            app._ecg_logger_instances,
            app._ecg_logger_threads,
            stop_event=app._ecg_logger_stop_event,
        )

        # Exit cleanly
        sys.exit(0)

    # Catch signals properly
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
    except RuntimeError as e:
        app.logger.error("Flask server error: %s", e, exc_info=True)
        shutdown()


if __name__ == "__main__":
    main()
