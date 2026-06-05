from pydantic_settings import BaseSettings, SettingsConfigDict

class CBSConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CBS_")

    tb_addresses: str                          # "tigerbeetle:3001"
    pg_dsn: str                                # "postgres://cbs:cbs_dev@..."
    port: int = 8080
    log_level: str = "info"
    pg_pool_max: int = 10
    cache_ttl_fx: int = 30                     # seconds
    cache_ttl_product: int = 300               # seconds
