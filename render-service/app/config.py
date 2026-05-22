"""Application settings loaded from environment."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Auth
    render_api_key: str = "changeme"
    log_level: str = "INFO"

    # Workspace
    workspace_dir: str = "/workspace"

    # TTS
    azure_speech_key: str = ""
    azure_speech_region: str = "westeurope"
    tts_default_voice: str = "es-ES-AlvaroNeural"
    tts_language: str = "es-ES"

    # Stock images
    unsplash_access_key: str = ""
    pixabay_api_key: str = ""
    pexels_api_key: str = ""

    # Storage (S3-compatible)
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "horror-assets"
    minio_secure: bool = False

    # DB / Redis
    database_url: str = ""
    redis_url: str = "redis://redis:6379/0"


settings = Settings()
