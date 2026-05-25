from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telnyx_api_key: str = ""
    telnyx_app_id: str = ""
    telnyx_from_number: str = ""

    deepgram_api_key: str = ""

    llm_provider: str = "groq"
    groq_api_key: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    bedrock_model: str = "anthropic.claude-haiku-4-5-v1:0"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    neo4j_client_id: str = "gi-client-001"

    redis_url: str = "redis://localhost:6379"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "nextservices-comms"
    minio_use_ssl: bool = False

    transcript_encryption_key: str = ""

    host: str = "0.0.0.0"
    port: int = 8090
    public_url: str = "http://localhost:8090"
    log_level: str = "INFO"


settings = Settings()
