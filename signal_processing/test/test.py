#!/usr/bin/env python3
import json
import pynng
import msgpack
import msgpack_numpy as m

with pynng.Sub0() as sub:
    topic = b'ecg.raw'
    sub.subscribe(topic)
    address = "tcp://localhost:9999"
    sub.dial(address)
    while True:
        try:
            msg = sub.recv()
            payload = msg[len(topic):]
            #data = msgpack.unpackb(msg, raw=False)
            data = msgpack.unpackb(payload, object_hook=m.decode)
            print(data)
        except (msgpack.ExtraData, msgpack.UnpackException) as e:
            print(f"Exception caught: {e}")
            print(f"Raw msg (first 16 bytes): {msg[:16]}")
            print(f"Topic: {topic}, length: {len(topic)}")
            continue

