"""One configuration reader per process; production never searches a local .env."""
import os
from pathlib import Path

from decouple import AutoConfig, Config, RepositoryEnv


config = AutoConfig(search_path=str(Path(__file__).resolve().parent.parent))


def use_production_environment():
    global config
    path = Path(os.environ.get(
        'FIBERGENIUS_ENV_FILE',
        r'C:\ProgramData\COTENER\FiberGenius\config\.env',
    ))
    if not path.is_file():
        raise RuntimeError(f'Fiber Genius environment file not found: {path}')
    # Config preserves environment-variable precedence over the selected file.
    config = Config(RepositoryEnv(str(path)))
    return str(path), config
