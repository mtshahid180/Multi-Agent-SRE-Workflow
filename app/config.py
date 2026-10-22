"""Application settings, loaded from environment variables / a local .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///./incidents.db"
    api_key: str | None = None

    # LLM
    llm_provider: str = "auto"  # auto | anthropic | openai
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str | None = None  # optional override
    llm_max_tokens: int = 4096
    llm_timeout_s: float = 90.0
    max_llm_calls_per_incident: int = 6

    # GitHub
    github_api_url: str = "https://api.github.com"
    github_app_id: str | None = None
    github_private_key_path: str | None = None
    github_installation_id: str | None = None
    github_token: str | None = None
    github_webhook_secret: str | None = None
    incident_label: str = "incident"
    pr_draft: bool = True

    # Scope and safety
    allowed_repos: str = ""
    local_repos_dir: str = "./examples"
    require_approval: bool = True
    auto_approve_low_risk: bool = False
    max_patch_files: int = 5
    max_patch_lines: int = 200
    max_context_files: int = 6
    max_file_chars: int = 20_000

    # Verification
    verify_workflow_file: str = "verify-patch.yml"
    max_fix_attempts: int = 2
    ci_timeout_s: int = 900
    ci_poll_interval_s: int = 15

    @property
    def allowed_repo_list(self) -> list[str]:
        return [r.strip() for r in self.allowed_repos.split(",") if r.strip()]

    @property
    def llm_provider_resolved(self) -> str:
        """'auto' picks the provider from the key prefix (sk-ant-... = Anthropic)."""
        provider = self.llm_provider.strip().lower()
        if provider in ("anthropic", "openai"):
            return provider
        key = self.llm_api_key or ""
        return "anthropic" if key.startswith("sk-ant-") else "openai" if key else "anthropic"

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
