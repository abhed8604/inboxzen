from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_laya_router = None


def get_laya_router(preload: bool = False, model: str = "english"):
    """Get or initialize the singleton Laya Router."""
    global _laya_router
    if _laya_router is None:
        try:
            from laya import Router
            _laya_router = Router(preload=False)
            logger.info("Initialized Laya Router")
        except Exception as e:
            logger.error("Failed to initialize Laya Router: %s", e)
            return None
    if preload and _laya_router is not None and not _laya_router.loaded:
        try:
            from app.services.llm_triage import normalize_laya_model
            m = normalize_laya_model(model)
            _laya_router.load(m)
            logger.info("Preloaded Laya model (%s) into memory", m)
        except Exception as e:
            logger.error("Failed to preload Laya: %s", e)
    return _laya_router


def unload_laya() -> bool:
    """Unload Laya model weights and release RAM/VRAM."""
    global _laya_router
    if _laya_router is not None and len(_laya_router.loaded) > 0:
        try:
            _laya_router.unload()
            import gc
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            logger.info("Unloaded Laya from memory")
            return True
        except Exception as e:
            logger.error("Failed to unload Laya: %s", e)
    return False


def load_laya(model: str = "english") -> bool:
    """Preload Laya model weights into memory, evicting any previous checkpoint."""
    router = get_laya_router(preload=False)
    if router is None:
        return False
    try:
        from app.services.llm_triage import normalize_laya_model
        m = normalize_laya_model(model)
        for loaded_name in list(router.loaded):
            if loaded_name != m:
                router.unload(loaded_name)
        router.load(m)
        logger.info("Loaded Laya model (%s) into memory", m)
        return True
    except Exception as e:
        logger.error("Failed to load Laya model: %s", e)
        return False


def is_laya_loaded() -> bool:
    """Check if any Laya model checkpoint is loaded in memory."""
    return _laya_router is not None and len(_laya_router.loaded) > 0
