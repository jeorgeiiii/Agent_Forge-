"""Worker tests run against fakes - never against settings from a developer's .env / terminal.

(VS Code can inject the repo's .env into terminals; a real INTERNAL_API_TOKEN would make the worker call
endpoints the fakes don't serve, e.g. the LLM quota check.)
"""

import os

for _name in ("INTERNAL_API_TOKEN", "AGENTFORGE_API_URL", "TEMPORAL_ADDRESS", "TEMPORAL_TASK_QUEUE"):
    os.environ.pop(_name, None)
