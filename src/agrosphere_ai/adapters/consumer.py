"""Private bounded collection-v2 sink. Confirm diagnosis or pause without ACK."""
from datetime import datetime, timezone
import json
import threading
import pika
from agrosphere_ai.adapters.codec import parse_event


class Consumer:
    def __init__(self, store, url, queue, diagnostics):
        self.store, self.url, self.queue, self.diagnostics = store, url, queue, diagnostics
        self.stop = threading.Event()
        self.paused = False
        self.counts = {"COMMITTED": 0, "DUPLICATE": 0, "CONFLICT": 0, "DIAGNOSTIC": 0}

    def run(self):
        connection = None
        try:
            connection = pika.BlockingConnection(pika.URLParameters(self.url))
            channel = connection.channel()
            channel.basic_qos(prefetch_count=1)
            channel.confirm_delivery()
            # Topology is provisioned by the owned fixture, never declared by this principal.
            for method, properties, body in channel.consume(self.queue, auto_ack=False, inactivity_timeout=1):
                if self.stop.is_set():
                    break
                self.store.guard.verify()
                if method is None:
                    continue
                try:
                    row = parse_event(body, datetime.now(timezone.utc))
                    result = self.store.ingest(row)
                except Exception as error:
                    # Unknown storage failures pause. Only validated parser/admission errors diagnose.
                    from jsonschema import ValidationError
                    if not isinstance(error, (ValueError, ValidationError)):
                        raise
                    result = "INVALID_OR_ADMISSION"
                if result in {"INVALID_OR_ADMISSION", "CONFLICT"}:
                    channel.basic_publish(exchange=self.diagnostics, routing_key="quarantine", body=json.dumps({"policy": "M4_MINIMAL_DIAGNOSTIC_V1", "reason": result}).encode(),
                                          properties=pika.BasicProperties(content_type="application/json", delivery_mode=2), mandatory=True)
                    self.counts["DIAGNOSTIC"] += 1
                if result in self.counts:
                    self.counts[result] += 1
                channel.basic_ack(method.delivery_tag)
        except Exception:
            # No payload/token/connection exception logging. Restart requires an explicit operator action.
            self.paused = True
        finally:
            if connection and connection.is_open:
                connection.close()
