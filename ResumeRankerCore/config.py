"""
Configuration loader for scoring system.
Loads scoring rubric, category weights, and validation rules.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Path to the scoring config file
_CONFIG_DIR = Path(__file__).parent
_SCORING_CONFIG_FILE = _CONFIG_DIR / "scoring_config.json"


def load_scoring_config() -> Dict[str, Any]:
    """
    Load scoring configuration from scoring_config.json.
    
    Returns:
        dict with 'scoreSystem' (str) and 'scoreMax' (dict) keys
    """
    try:
        with open(_SCORING_CONFIG_FILE, "r") as f:
            config = json.load(f)
        logger.info("Loaded scoring config from %s", _SCORING_CONFIG_FILE)
        return config
    except FileNotFoundError:
        logger.error("Scoring config file not found at %s", _SCORING_CONFIG_FILE)
        raise
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in scoring config: %s", e)
        raise


# Cache the config on first load
_CACHED_CONFIG = None


def get_score_system() -> str:
    """Get the cached scoring system prompt."""
    global _CACHED_CONFIG
    if _CACHED_CONFIG is None:
        _CACHED_CONFIG = load_scoring_config()
    return _CACHED_CONFIG["scoreSystem"]


def get_score_max() -> Dict[str, int]:
    """Get the cached score maximum values per category."""
    global _CACHED_CONFIG
    if _CACHED_CONFIG is None:
        _CACHED_CONFIG = load_scoring_config()
    return _CACHED_CONFIG["scoreMax"]
