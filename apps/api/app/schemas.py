from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


NodeType = str


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=100)
    type: str = Field(min_length=1, max_length=100)
    label: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)


class GraphDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    nodes: list[GraphNode] = Field(min_length=1)
    edges: list[GraphEdge] = Field(default_factory=list)
    positions: dict[str, Any] | None = None


class GraphCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    # Omit to start from the default Start -> End graph.
    definition: GraphDefinition | None = None
    workspace_id: int | None = None


class GraphUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    definition: GraphDefinition


class GraphRead(BaseModel):
    id: int
    name: str
    description: str
    definition: GraphDefinition
    workspace_id: int | None = None
    version: int = 1
    published_version: int | None = None
    is_active: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GraphRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class GraphDuplicate(BaseModel):
    name: str | None = Field(default=None, max_length=200)


class GraphPublish(BaseModel):
    version: int | None = None  # default: latest


class GraphVersionRead(BaseModel):
    id: int
    graph_id: int
    version: int
    name: str
    description: str
    definition: dict[str, Any]
    compiled_hash: str | None = None
    created_by: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RunCreate(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


class RunRead(BaseModel):
    id: int
    graph_id: int
    workflow_id: str
    status: str
    input: dict[str, Any]
    created_at: datetime
    graph_version: int | None = None
    trigger_kind: str = "manual"
    output: Any = None
    error: str | None = None
    finished_at: datetime | None = None
    retry_of_run_id: int | None = None

    model_config = ConfigDict(from_attributes=True)


class RunDetail(RunRead):
    state: dict[str, Any] | None = None


class ApprovalDecision(BaseModel):
    node_id: str = Field(min_length=1, max_length=100)
    approved: bool
    comment: str = Field(default="", max_length=2000)


# ----- auth & workspaces -------------------------------------------------


class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)
    name: str = Field(default="", max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class UserRead(BaseModel):
    id: int
    email: str
    name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LoginResponse(BaseModel):
    user: UserRead
    token: str
    expires_at: datetime


class ApiKeyCreate(BaseModel):
    label: str = Field(default="", max_length=200)


class ApiKeyRead(BaseModel):
    id: int
    label: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ApiKeyCreated(ApiKeyRead):
    key: str  # shown once


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceRead(BaseModel):
    id: int
    name: str
    role: str
    created_at: datetime


class MemberAdd(BaseModel):
    user_id: int | None = None
    email: str | None = None
    role: str = "editor"


class MemberRoleUpdate(BaseModel):
    role: str


class MemberRead(BaseModel):
    user_id: int
    email: str
    name: str
    role: str


# ----- triggers ----------------------------------------------------------


class TriggerRead(BaseModel):
    id: int
    node_id: str
    kind: str
    active: bool
    url: str | None = None
    config: dict[str, Any]
    schedule_id: str | None = None


class ChatTriggerMessage(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    session_id: str = Field(min_length=1, max_length=100)
    wait: bool = True


# ----- credentials -------------------------------------------------------


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str
    data: dict[str, Any]
    workspace_id: int | None = None


class CredentialUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    data: dict[str, Any] | None = None


class CredentialRead(BaseModel):
    id: int
    name: str
    type: str
    workspace_id: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OAuthStart(BaseModel):
    provider: str = ""
    connector: str | None = None  # integration id (google_sheets ...): provider + scopes come from its definition
    credential_name: str = Field(min_length=1, max_length=200)
    workspace_id: int | None = None
    scopes: list[str] | None = None


# ----- copilot -----------------------------------------------------------


class CopilotRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10000)
    # optional: give the copilot the graph being edited as context
    graph: dict[str, Any] | None = None


class CopilotGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10000)


class CopilotEditRequest(BaseModel):
    definition: dict[str, Any]
    instruction: str = Field(min_length=1, max_length=10000)
    selected_node_id: str | None = None


class CopilotPatchRequest(BaseModel):
    definition: dict[str, Any]
    patch: dict[str, Any]


class CopilotExplainRequest(BaseModel):
    definition: dict[str, Any]
    node_id: str


class CopilotFixRunRequest(BaseModel):
    graph_id: int
    run_id: int


class CopilotResponse(BaseModel):
    content: str


class ChatMessageCreate(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=20)
    content: str = Field(min_length=1, max_length=50000)
    graph_data: dict[str, Any] | None = None


class ChatMessageRead(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    graph_data: dict[str, Any] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

