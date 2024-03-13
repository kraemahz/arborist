import argparse
import json
import logging
import os
import sys
import time

from dataclasses import dataclass
from queue import Queue

from arborist import __version__
from arborist.event import PrismConfig, connect_to_event_listener
from arborist.actions import ActionsThread

__author__ = "Teague Lasser"
__copyright__ = "Teague Lasser"
__license__ = "MIT"

_logger = logging.getLogger(__name__)


def parse_args(args) -> argparse.Namespace:
    """Parse command line parameters"""
    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        dest="config", help="Configuration file", type=argparse.FileType("r")
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"arborist {__version__}",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        dest="loglevel",
        help="set loglevel to INFO",
        action="store_const",
        const=logging.INFO,
    )
    return parser.parse_args(args)


def setup_logging(loglevel):
    """Setup basic logging

    Args:
      loglevel (int): minimum loglevel for emitting messages
    """
    logformat = "[%(asctime)s] %(levelname)s:%(name)s:%(message)s"
    logging.basicConfig(
        level=loglevel,
        stream=sys.stdout,
        format=logformat,
        datefmt="%Y-%m-%d %H:%M:%S"
    )


@dataclass
class Config:
    prism: PrismConfig

    def __init__(self, prism):
        self.prism = PrismConfig(**prism)


def main(args):
    """Main function"""
    args = parse_args(args)
    setup_logging(args.loglevel)
    config = Config(**json.load(args.config))
    queue = Queue()
    client = connect_to_event_listener(config.prism, queue)
    token = os.getenv('ARBORIST_GITHUB_TOKEN')
    assert token is not None, "ARBORIST_GITHUB_TOKEN token must be set"
    thread = ActionsThread(client, queue, token)
    thread.start()

    try:
        while True:
            time.sleep(360)
    except KeyboardInterrupt:
        pass
    finally:
        _logger.info("Performing job cleanup...")
        queue.put(None)
        thread.join()


def run():
    """Calls :func:`main` passing the CLI arguments extracted from :obj:`sys.argv`

    This function can be used as entry point to create console scripts with setuptools.
    """
    main(sys.argv[1:])


if __name__ == "__main__":
    run()
