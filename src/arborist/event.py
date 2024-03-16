import json
import logging

from dataclasses import asdict, dataclass
from queue import Queue
from typing import Any, List

from prism import Client, Wavelet

_log = logging.getLogger(__name__)

SECRET_CREATED_BEAM = "urn:subseq.io:repos:private-key:created"
REPO_CREATED_BEAM = "urn:subseq.io:repos:repo:created"


@dataclass
class PrismConfig:
    addr: str
    events: List[str]


def push_job_result(client: Client, result: Any, source: str):
    result = asdict(result)
    json_bytes = json.dumps(result)
    client.emit(source, json_bytes)


def connect_to_event_listener(config: PrismConfig, queue: Queue):
    def event_handler(wavelet: Wavelet):
        for photon in wavelet.photons:
            data = photon.payload
            try:
                data = json.loads(data)
            except ValueError:
                _log.warn("Received unparsable message on %s", wavelet.beam)
            queue.put(data)

    client = Client(f"ws://{config.addr}", event_handler)
    for event_sink in config.events:
        client.subscribe(event_sink)

    return client
