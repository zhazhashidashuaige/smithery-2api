import json
import logging
from pathlib import Path
from threading import Lock
from typing import Dict, Iterable, List, Optional, Sequence

from app.core.config import settings

logger = logging.getLogger(__name__)


def _normalize_models(models: Iterable[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for item in models:
        if not isinstance(item, str):
            continue
        candidate = item.strip()
        if not candidate:
            continue
        if candidate in seen:
            continue
        normalized.append(candidate)
        seen.add(candidate)
    return normalized


class ModelVisibilityStore:
    """Runtime storage for configuring which models should be hidden from clients."""

    def __init__(
        self,
        known_models: Sequence[str],
        default_hidden_models: Sequence[str],
        storage_path: Optional[str] = None,
    ) -> None:
        self._lock = Lock()
        self._known_models = _normalize_models(known_models)
        self._storage_path = Path(storage_path).expanduser() if storage_path else None

        default_hidden = set(model for model in default_hidden_models if model in self._known_models)
        persisted = self._load_from_disk()
        if persisted is not None:
            default_hidden = persisted & set(self._known_models)

        self._hidden_models = default_hidden

    def _load_from_disk(self) -> Optional[set[str]]:
        if not self._storage_path:
            return None
        try:
            if not self._storage_path.exists():
                return None
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(model for model in data if isinstance(model, str))
        except Exception:
            logger.exception("无法从磁盘加载隐藏模型配置，使用默认配置。")
        return None

    def _persist(self) -> None:
        if not self._storage_path:
            return
        try:
            if not self._storage_path.parent.exists():
                self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = [model for model in self._known_models if model in self._hidden_models]
            self._storage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logger.exception("写入隐藏模型配置失败。")

    @property
    def known_models(self) -> List[str]:
        return list(self._known_models)

    @property
    def hidden_models(self) -> List[str]:
        with self._lock:
            return [model for model in self._known_models if model in self._hidden_models]

    @property
    def visible_models(self) -> List[str]:
        with self._lock:
            return [model for model in self._known_models if model not in self._hidden_models]

    def is_hidden(self, model: str) -> bool:
        with self._lock:
            return model in self._hidden_models

    def set_hidden(self, hidden_models: Iterable[str]) -> List[str]:
        normalized = [model for model in _normalize_models(hidden_models) if model in self._known_models]
        with self._lock:
            self._hidden_models = set(normalized)
            payload = [model for model in self._known_models if model in self._hidden_models]
        self._persist()
        settings.HIDDEN_MODELS = payload
        return payload

    def describe(self) -> Dict[str, List[str]]:
        with self._lock:
            return {
                "known_models": list(self._known_models),
                "visible_models": [model for model in self._known_models if model not in self._hidden_models],
                "hidden_models": [model for model in self._known_models if model in self._hidden_models],
            }


model_visibility_store = ModelVisibilityStore(
    known_models=settings.KNOWN_MODELS,
    default_hidden_models=settings.HIDDEN_MODELS,
    storage_path=settings.MODEL_VISIBILITY_PATH,
)

settings.HIDDEN_MODELS = model_visibility_store.hidden_models
