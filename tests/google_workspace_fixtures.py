"""Synthetic Google fixtures. Never uses an actual account or OS vault."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from jarvis_agent.google_workspace_catalog import CLIENT_SLOT, GOOGLE_TARGET, IDENTITY_SCOPES, SCOPES, SERVICES, admitted_operations
from jarvis_agent.google_workspace_connection import snapshot
from jarvis_agent.google_workspace_oauth import account_record
from jarvis_agent.mcp_credentials import CredentialVault
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.personal_workspace import PersonalWorkspaceStore

CLIENT = {"client_id": "1234567890-fixture.apps.googleusercontent.com", "client_secret": "PRIVATE_CLIENT_FIXTURE", "project_id": "jarvis-fixture"}
PROFILE = {"id": "google-fixture-subject", "email": "owner@example.test", "verified_email": True}


class MemoryStore:
    def __init__(self):
        self.items = {}
    def get(self, key):
        return self.items.get(key)
    def put(self, key, value):
        self.items[key] = value
    def delete(self, key):
        self.items.pop(key, None)


def bundle(profile=None, scopes=None, expires=100000000000):
    return {"version": 1, "client": dict(CLIENT), "account": account_record(profile or PROFILE),
        "token": {"access_token": "PRIVATE_ACCESS_FIXTURE", "refresh_token": "PRIVATE_REFRESH_FIXTURE",
            "expires_at": expires, "scopes": sorted(scopes if scopes is not None else IDENTITY_SCOPES | set(SCOPES.values()))}}


class GoogleFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = MCPRegistry(self.root / "mcp.json")
        self.workspace = PersonalWorkspaceStore(self.root / "workspace.sqlite3")
        self.vault = CredentialVault(self.root / "secrets", backend=MemoryStore())

    def seed_client(self):
        self.vault.write(CLIENT_SLOT, GOOGLE_TARGET, json.dumps(CLIENT), "oauth_client")
        self.registry.note_google_client(project_id=CLIENT["project_id"], fingerprint=hashlib.sha256(CLIENT["client_id"].encode()).hexdigest())

    def packet(self):
        state = snapshot(self.registry, self.workspace)
        return json.dumps({k: state[k] for k in ("client_fingerprint", "binding_revisions")})

    def seed_account(self, data=None):
        data = data or bundle()
        sid = "google_" + data["account"]["subject_hash"][:16]
        ops = admitted_operations(data["token"]["scopes"])
        services = [s for s in SERVICES if any(op.service == s for op in ops)]
        self.registry.install_google_account(sid, account=data["account"], tools=[op.definition() for op in ops], services=services)
        self.vault.write(sid, GOOGLE_TARGET, json.dumps(data), "oauth_tokens")
        self.workspace.bind_services(services, sid, expected_revisions={s: 0 for s in SERVICES})
        return {**self.registry.get_server(sid), "id": sid}
