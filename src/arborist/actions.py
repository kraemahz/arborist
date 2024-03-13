import logging

from dataclasses import dataclass
from pathlib import Path
from queue import Empty
from subprocess import check_call, CalledProcessError
from tempfile import TemporaryDirectory
from threading import Thread
from typing import Dict, List

import requests

QUEUE_GET_TIMEOUT = 3.0
_log = logging.getLogger(__name__)


class ActionsThread(Thread):

    def __init__(self, client, queue, token):
        super().__init__()
        self.client = client
        self.queue = queue
        self.token = token
        self.running = True

    def run(self):
        while self.running:
            try:
                next_item = self.queue.get(timeout=QUEUE_GET_TIMEOUT)
            except Empty:
                continue

            action = Action.from_dict(next_item)
            if action.action_type == "CreateRepo":
                self.create_repo(**action.action_content)
            elif action.action_type == "AddDeployKey":
                self.add_deploy_key_to_repo(**action.action_content)

    def create_repo(self,
                    name: str,
                    description: str,
                    private: bool,
                    org: None | str,
                    setup_types: List) -> Dict:
        url = ("https://api.github.com/user/repos"
               if org is None else
               f"https://api.github.com/orgs/{org}/repos")
        headers = {"Authorization": f"token {self.token}",
                   "Accept": "application/vnd.github.v3+json"}

        data = {"name": name, "description": description, "private": private}
        response = requests.post(url, headers=headers, json=data)

        if response.status_code >= 300:
            _log.error("Failed to create repository %s\n%s\n%s",
                       name, response.status_code, response.content)
            return None

        response = response.json()
        full_name = response["full_name"]
        self.git_setup(name, full_name, setup_types)

    def add_deploy_key_to_repo(self,
                               owner: str,
                               repo: str,
                               key_title: str,
                               public_key: str,
                               read_only: bool) -> Dict:

        url = f"https://api.github.com/repos/{owner}/{repo}/keys"
        headers = {"Authorization": f"token {self.token}",
                   "Accept": "application/vnd.github.v3+json"}
        data = {"title": key_title, "key": public_key, "read_only": read_only}
        response = requests.post(url, headers=headers, json=data)
        return response.json()


def git_setup(name, full_name, setup_types):
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / name
        path.mkdir()
        abs_path = str(path.absolute())
        try:
            check_call(["git", "init"], cwd=abs_path)
        except CalledProcessError:
            _log.error("Failed to git init repository %s", full_name)
            return None

        try:
            check_call(["git", "branch", "-M", "main"], cwd=abs_path)
        except CalledProcessError:
            _log.error("Failed to git set branch to main %s", full_name)
            return None

        try:
            check_call(["git", "remote", "add", "origin",
                        f"git@github.com:{full_name}.git"], cwd=abs_path)
        except CalledProcessError:
            _log.error("Failed to git remote add origin %s", full_name)
            return None

        for setup_type in setup_types:
            setup_default(path, setup_type)

            try:
                check_call(["git", "add", "."], cmd=abs_path)
            except CalledProcessError:
                _log.error("Failed to git add files %s", full_name)
                return None

            try:
                check_call(["git", "commit", "-m", f"INIT: {setup_type}"],
                           cmd=abs_path)
            except CalledProcessError:
                _log.error("Failed to git commit new files %s", full_name)
                return None

        try:
            check_call(["git", "push", "-u", "origin", "main"], cwd=abs_path)
        except CalledProcessError:
            _log.error("Failed to git push %s", full_name)
            return None


def setup_default(path: Path, setup_type: str):
    pass


@dataclass
class Action:
    action_type: str
    action_content: dict

    @classmethod
    def from_dict(cls, action: dict):
        for key, value in action.items():
            return cls(key, value)
