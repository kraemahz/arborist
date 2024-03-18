import logging

from dataclasses import dataclass
from pathlib import Path
from queue import Empty
from subprocess import check_call, CalledProcessError
from tempfile import TemporaryDirectory
from threading import Thread
from typing import Dict, List
from typing_extensions import Self

import requests

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from .event import push_job_result, REPO_CREATED_BEAM, SECRET_CREATED_BEAM

QUEUE_GET_TIMEOUT = 3.0
_log = logging.getLogger(__name__)


@dataclass
class Project:
    id: str
    name: str
    description: str

    @classmethod
    def from_dict(cls, source: Dict) -> Self:
        return cls(source["id"],
                   source["name"],
                   source["description"])


class ActionsThread(Thread):

    def __init__(self, client, queue, token, conf):
        super().__init__()
        self.client = client
        self.queue = queue
        self.token = token
        self.conf = conf
        self.user_name = self.get_user_info()
        self.running = True

    def run(self):
        while self.running:
            try:
                next_item = self.queue.get(timeout=QUEUE_GET_TIMEOUT)
            except Empty:
                continue

            project = Project.from_dict(next_item)

            repo = self.create_repo(project)
            if repo is None:
                continue

            public_key, private_key = generate_key_pair()
            self.add_deploy_key_to_repo(repo.full_name, public_key)
            self.store_private_key(repo, private_key)

    def store_private_key(self, repo, private_key):
        secret_name = "key-" + repo.full_name.replace('/', '-')
        new_secret = Secret(self.conf.kubernetes_namespace,
                            secret_name,
                            repo.project_id)
        new_secret.create(data={"private-key": private_key})
        push_job_result(self.client, new_secret, SECRET_CREATED_BEAM)

    def get_user_info(self):
        url = 'https://api.github.com/user'
        headers = {
            'Authorization': f'token {self.token}',
            'Accept': 'application/vnd.github.v3+json',
        }
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json().get('name')

    def create_repo(self, project: Project) -> Dict:
        private = self.conf.private
        org = self.conf.org
        setup_types = ([] if self.conf.setup_types is None
                       else self.conf.setup_types)
        url = ("https://api.github.com/user/repos"
               if org is None else
               f"https://api.github.com/orgs/{org}/repos")
        headers = {"Authorization": f"token {self.token}",
                   "Accept": "application/vnd.github.v3+json"}

        repo_name = to_repo_name(project.name)
        data = {"name": repo_name,
                "description": project.description[:350],
                "private": private}
        response = requests.post(url, headers=headers, json=data)

        if response.status_code >= 300:
            if response.status_code < 500:
                if any('already exists' in error['message']
                       for error in response.json().get('errors', [])):
                    full_name = (f"{org}/{repo_name}"
                                 if org is not None else
                                 f"{self.user_name}/{repo_name}")
                    _log.info("Repository %s already exists", full_name)
                    repo = Repo(full_name,
                                to_repo_url(full_name),
                                project.id)
                    push_job_result(self.client, repo, REPO_CREATED_BEAM)
                    return repo

            _log.error("Failed to create repository %s\n%s",
                       repo_name,
                       response.content)
            return None

        response = response.json()
        full_name = response["full_name"]
        repo_url = git_setup(repo_name, full_name, setup_types)
        if repo_url is None:
            _log.error("Setting up git repository failed")
            return None

        repo = Repo(full_name, repo_url, project.id)
        push_job_result(self.client, repo, REPO_CREATED_BEAM)
        return repo

    def add_deploy_key_to_repo(self, full_name: str, public_key: str) -> Dict:
        url = f"https://api.github.com/repos/{full_name}/keys"
        headers = {"Authorization": f"token {self.token}",
                   "Accept": "application/vnd.github.v3+json"}
        data = {"title": self.conf.key_title,
                "key": public_key,
                "read_only": False}
        response = requests.post(url, headers=headers, json=data)
        return response.json()


def to_repo_name(name: str) -> str:
    return name.lower().replace(" ", "_")


def generate_key_pair() -> (str, str):
    # Generate a private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
        backend=default_backend()
    )

    # Generate the public key
    public_key = private_key.public_key()

    # Convert the private key to its PEM format
    pem_private_key = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')

    # Convert the public key to its PEM format
    pem_public_key = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')

    return pem_public_key, pem_private_key


def to_repo_url(full_name: str) -> str:
    return f"git@github.com:{full_name}.git"


def run_checked_call(action: List[str], path: Path) -> bool:
    try:
        _log.info("Run: %s", " ".join(action))
        check_call(action, cwd=str(path))
    except CalledProcessError:
        return False
    return True


def git_setup(name, full_name, setup_types) -> str | None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / name
        path.mkdir()
        abs_path = path.absolute()
        _log.info(f"cwd: {abs_path}")
        if not run_checked_call(["git", "init"], abs_path):
            _log.error("Failed to git init repository %s", full_name)
            return None
        if not run_checked_call(["git", "branch", "-M", "main"], abs_path):
            _log.error("Failed to git set branch to main %s", full_name)
            return None

        repo_url = to_repo_url(full_name)
        if not run_checked_call(["git", "remote", "add", "origin", repo_url],
                                abs_path):
            _log.error("Failed to git remote add origin %s", full_name)
            return None

        for setup_type in setup_types:
            if not setup_default(path, setup_type):
                _log.error("Failed to setup %s", setup_type)
                continue

            if not run_checked_call(["git", "add", "."], abs_path):
                _log.error("Failed to git add files %s", full_name)
                continue

            if not run_checked_call(
                    ["git", "commit", "-m", f'"INIT: {setup_type}"'],
                    abs_path):
                _log.error("Failed to git commit new files %s", full_name)
                continue

        if not run_checked_call(["git", "push", "-u", "origin", "main"],
                                abs_path):
            _log.error("Failed to git push %s", full_name)
            return None

    return repo_url


def setup_default(path: Path, setup_type: str):
    _log.info("Setup %s", setup_type)
    if setup_type == 'frontend':
        return run_checked_call(
            ["npm", "init", "vite@latest", path.name, "--",
             "--template", "react-ts"],
            path)
    elif setup_type == 'backend':
        return run_checked_call(["cargo", "init"], path)

    return False


@dataclass
class Repo:
    full_name: str
    repo_url: str
    project_id: str


@dataclass
class Secret:
    namespace: str
    name: str
    project_id: str

    def create(self, data: dict):
        config.load_incluster_config()
        api_instance = client.CoreV1Api()
        body = client.V1Secret(
            metadata=client.V1ObjectMeta(name=self.name),
            string_data=data  # Data should be a dict with key-value pairs
        )
        try:
            api_response = api_instance.create_namespaced_secret(
                self.namespace,
                body
            )
            _log.info(f"Secret created. Name: {self.name}")
            return api_response
        except ApiException as e:
            _log.error(f"Exception when calling create_namespaced_secret: {e}")


@dataclass
class ActionsConfig:
    private: bool
    key_title: str
    kubernetes_namespace: str
    org: str | None
    setup_types: List[str] | None

    def __init__(self,
                 private: bool,
                 key_title: str,
                 kubernetes_namespace: str,
                 org: str | None = None,
                 setup_types: List[str] | None = None):
        self.private = private
        self.key_title = key_title
        self.kubernetes_namespace = kubernetes_namespace
        self.org = org
        self.setup_types = setup_types
