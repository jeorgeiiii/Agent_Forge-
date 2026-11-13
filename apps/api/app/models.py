from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB, "postgresql")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# Cluster 1 - users, sessions, API keys
# --------------------------------------------------------------------------- #


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    # only a SHA-256 of the token is stored; the raw token lives in the user's cookie
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)


class ApiKeyRecord(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- #
# Cluster 2 - workspaces & memberships
# --------------------------------------------------------------------------- #


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Cluster 18: per-workspace overrides of the retention defaults (None = use defaults)
    retention: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    # Cluster 13: operator-set quota overrides (None = platform defaults)
    limits: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    # Cluster 16: soft delete (restorable until retention purges it)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InvitationRecord(Base):
    """Cluster 16 - an emailed invite to join a workspace. Only the token's SHA-256 is stored."""

    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MembershipRecord(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_membership"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # owner | editor | viewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


# --------------------------------------------------------------------------- #
# Cluster 3 - graphs & versions
# --------------------------------------------------------------------------- #


class GraphRecord(Base):
    __tablename__ = "graphs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Native JSONB on PostgreSQL, standard JSON fallback on SQLite
    definition: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    # Columns below were added later; server defaults let the startup migration add them.
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id"), index=True, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    runs: Mapped[list["RunRecord"]] = relationship(back_populates="graph", cascade="all, delete-orphan")
    versions: Mapped[list["GraphVersionRecord"]] = relationship(back_populates="graph", cascade="all, delete-orphan")


class GraphVersionRecord(Base):
    __tablename__ = "graph_versions"
    __table_args__ = (UniqueConstraint("graph_id", "version", name="uq_graph_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    graph_id: Mapped[int] = mapped_column(ForeignKey("graphs.id", ondelete="CASCADE"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    compiled_ir: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    compiled_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    graph: Mapped[GraphRecord] = relationship(back_populates="versions")


# --------------------------------------------------------------------------- #
# Cluster 6 - runs
# --------------------------------------------------------------------------- #


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    graph_id: Mapped[int] = mapped_column(ForeignKey("graphs.id"), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    graph_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger_kind: Mapped[str] = mapped_column(String(30), default="manual", server_default=text("'manual'"), nullable=False)
    state: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    output: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_of_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # set by retry_run
    compacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # cluster 18

    graph: Mapped[GraphRecord] = relationship(back_populates="runs")


class RunEventRecord(Base):
    """Cluster 11 - append-only event log of a run (what the live view streams).

    ``id`` is the global stream cursor (SSE ``Last-Event-ID``). ``source`` is who
    produced the event ("" = the run itself, else a sub-agent's workflow id) and
    ``seq`` that producer's counter; (run, source, seq) is unique, so retried
    deliveries are dropped.
    """

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "source", "seq", name="uq_run_event_source_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(250), default="", server_default=text("''"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    node_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    at: Mapped[str | None] = mapped_column(String(40), nullable=True)  # when it happened (workflow clock, ISO)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


# --------------------------------------------------------------------------- #
# Cluster 8 - triggers
# --------------------------------------------------------------------------- #


class TriggerRecord(Base):
    __tablename__ = "triggers"
    __table_args__ = (UniqueConstraint("graph_id", "node_id", name="uq_trigger_node"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    graph_id: Mapped[int] = mapped_column(ForeignKey("graphs.id", ondelete="CASCADE"), index=True, nullable=False)
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # webhook | schedule | chat
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    schedule_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


# --------------------------------------------------------------------------- #
# Cluster 9 - credentials
# --------------------------------------------------------------------------- #


class CredentialRecord(Base):
    __tablename__ = "credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # api_key | bearer | basic | oauth2 | custom
    encrypted_data: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class OAuthStateRecord(Base):
    __tablename__ = "oauth_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    credential_name: Mapped[str] = mapped_column(String(200), nullable=False)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    encrypted_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --------------------------------------------------------------------------- #
# Chat memory (existing; cluster 12 will extend it)
# --------------------------------------------------------------------------- #


class AuditEventRecord(Base):
    """Cluster 17 - append-only, hash-chained audit log.

    Each row's ``hash`` = sha256(prev_hash + canonical JSON of the row), chained per
    ``chain`` (one chain per workspace, plus "global"). Editing or deleting any row
    breaks every hash after it, which ``verify_audit_chain`` detects.
    """

    __tablename__ = "audit_events"
    # one row per (chain, position): two concurrent writers can never both append position N
    __table_args__ = (UniqueConstraint("chain", "position", name="uq_audit_chain_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 2, 3 ... within the chain
    workspace_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)  # user | api_key | anonymous | system
    actor_label: Mapped[str] = mapped_column(String(320), default="", nullable=False)  # email / key prefix
    action: Mapped[str] = mapped_column(String(60), index=True, nullable=False)  # e.g. graph.update
    resource_type: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), default="success", nullable=False)  # success | denied | failure
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)  # ISO-8601 UTC, part of the hash
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class AuditChainHead(Base):
    """Last position + hash of each chain; updated with compare-and-swap so concurrent writers cannot fork it."""

    __tablename__ = "audit_chain_heads"

    chain: Mapped[str] = mapped_column(String(40), primary_key=True)
    last_position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Retention: rows up to pruned_position were deleted; pruned_hash is the hash of the last deleted
    # row, so verification can still start from a known anchor.
    pruned_position: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    pruned_hash: Mapped[str] = mapped_column(String(64), default="0" * 64, server_default=text("'" + "0" * 64 + "'"),
                                             nullable=False)


class FileRecord(Base):
    """Cluster 19 - a stored file. Bytes live in the storage backend (local disk or S3); this is the metadata."""

    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4: unguessable, safe in URLs
    workspace_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    graph_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)  # produced by a run
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ready", nullable=False)  # pending (presigned upload) | ready
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemorySessionRecord(Base):
    """Cluster 12 - index of agent conversations (the memory key itself is an opaque hash)."""

    __tablename__ = "memory_sessions"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    graph_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    scope: Mapped[str] = mapped_column(String(250), default="", nullable=False)  # agent path, e.g. boss/researcher
    session_id: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ChatMessageRecord(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user, assistant, system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    graph_data: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


# --------------------------------------------------------------------------- #
# SSO step 1 - company login connections, company email domains, linked identities
# --------------------------------------------------------------------------- #


class SsoConnectionRecord(Base):
    """How a workspace's people log in through their company (one connection per workspace).

    The client secret is encrypted with the credentials vault and never returned by the API.
    """

    __tablename__ = "sso_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True,
                                              index=True, nullable=False)
    protocol: Mapped[str] = mapped_column(String(10), nullable=False)          # oidc | saml
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)     # shown on the login button
    issuer: Mapped[str] = mapped_column(String(500), default="", nullable=False)  # OIDC issuer / SAML entity id
    client_id: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    encrypted_client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)  # endpoints, SAML cert, scopes...
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enforce_sso: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # no password login for the domains
    allow_jit: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)     # create users on first login
    default_role: Mapped[str] = mapped_column(String(20), default="viewer", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class SsoDomainRecord(Base):
    """An email domain a workspace claims (e.g. acme.com). Only *verified* domains send people to SSO.

    Proof of ownership: a DNS TXT record `_agentforge-challenge.<domain>` = `agentforge-verify=<token>`.
    Several workspaces may claim a domain, but only one can have it verified.
    """

    __tablename__ = "sso_domains"
    __table_args__ = (UniqueConstraint("workspace_id", "domain", name="uq_sso_domain_workspace"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False)
    domain: Mapped[str] = mapped_column(String(253), index=True, nullable=False)
    verification_token: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserIdentityRecord(Base):
    """Links an AgentForge user to their account at the company login (the IdP's stable subject id)."""

    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("connection_id", "subject", name="uq_identity_subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    connection_id: Mapped[int] = mapped_column(ForeignKey("sso_connections.id", ondelete="CASCADE"), index=True,
                                               nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DataSourceRecord(Base):
    """A company's own database, connected READ-ONLY so agents can look things up (services/data_sources.py).

    The connection address (with its password) is encrypted with the vault key and never sent to the browser
    or the worker: queries run inside the API, which checks the workflow's workspace first.
    """

    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # postgres | mysql | sqlite (tests/dev only)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)  # what the data is - shown to agents
    encrypted_url: Mapped[str] = mapped_column(Text, nullable=False)
    max_rows: Mapped[int] = mapped_column(Integer, default=200, nullable=False)  # per query
    # cached table/column list (refreshed by "Test connection")
    schema_cache: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    # data dictionary: where each standard business concept lives in THIS company's tables, its policies and the
    # "today" date for reports (see agentforge_core/business_concepts.py and validate_dictionary)
    dictionary: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
