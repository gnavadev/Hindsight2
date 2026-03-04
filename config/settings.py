"""
Configuration Management

Handles loading/saving settings and API keys with encryption.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional
from cryptography.fernet import Fernet
import base64
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class Settings:
    """Application settings manager"""

    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.config_dir = Path.home() / ".privacy_llm_assistant"
        self.config_path = self.config_dir / config_file

        # Ensure config directory exists
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Encryption key (stored separately)
        self.key_file = self.config_dir / ".key"
        self._cipher = self._get_cipher()

        # Load settings
        self.settings = self._load_settings()

        # Load environment variables with override=True to prevent stale IDE caching
        load_dotenv(self.config_dir / ".env", override=True)
        load_dotenv(override=True)

        # Override Settings with Environment Variables
        env_model = os.getenv("GEMINI_MODEL")
        if env_model:
            if "llm" not in self.settings:
                self.settings["llm"] = {}
            self.settings["llm"]["gemini_model"] = env_model
            logger.debug(f"GEMINI_MODEL override from env: {env_model}")

    def _get_cipher(self) -> Fernet:
        """Get or create encryption cipher"""
        if self.key_file.exists():
            with open(self.key_file, 'rb') as f:
                key = f.read()
        else:
            # Generate new key
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(key)
            logger.info("Generated new encryption key")

        return Fernet(key)

    def _load_settings(self) -> Dict[str, Any]:
        """Load settings from file"""
        if not self.config_path.exists():
            logger.info("No config file found, using defaults")
            return self._get_default_settings()

        try:
            with open(self.config_path, 'r') as f:
                logger.debug(f"Loaded config from {self.config_path}")
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading config: {e}", exc_info=True)
            return self._get_default_settings()

    def _get_default_settings(self) -> Dict[str, Any]:
        """Get default settings"""
        return {
            "llm": {
                "provider": "gemini",  # or "claude"
                "gemini_api_key_encrypted": None,
                "claude_api_key_encrypted": None,
                "gemini_model": "gemini-3.1-flash-lite-preview",
                "claude_model": "claude-3-5-sonnet-20241022"
            },
            "ui": {
                "theme": "dark",
                "window_position": None,
                "window_size": [1400, 900],
                "sidebar_width": 250
            },
            "shortcuts": {
                "screenshot": "Ctrl+H",
                "hide_window": "Ctrl+B",
                "send_message": "Ctrl+Enter"
            },
            "privacy": {
                "click_through_default": False
            }
        }

    def save(self):
        """Save settings to file"""
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self.settings, f, indent=2)
            logger.info(f"Settings saved to {self.config_path}")
        except Exception as e:
            logger.error(f"Error saving config: {e}", exc_info=True)

    def encrypt_value(self, value: str) -> str:
        """Encrypt a sensitive value"""
        encrypted = self._cipher.encrypt(value.encode())
        return base64.b64encode(encrypted).decode()

    def decrypt_value(self, encrypted_value: str) -> Optional[str]:
        """Decrypt a sensitive value"""
        try:
            encrypted_bytes = base64.b64decode(encrypted_value.encode())
            decrypted = self._cipher.decrypt(encrypted_bytes)
            return decrypted.decode()
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            return None  # Return None instead of a string so fallback logic works

    def set_api_key(self, provider: str, api_key: str):
        """Set and encrypt an API key"""
        encrypted = self.encrypt_value(api_key)
        self.settings["llm"][f"{provider}_api_key_encrypted"] = encrypted
        self.save()
        logger.info(f"API key stored for {provider}")

    def get_api_key(self, provider: str) -> Optional[str]:
        """Get an API key (prioritizing Environment Variables over config)"""
        # 1. Try Environment Variable first (allows .env to override broken configs)
        env_key = os.getenv(f"{provider.upper()}_API_KEY")
        if env_key:
            logger.debug(f"Using {provider} API key from environment variable")
            return env_key

        # 2. Try encrypted key
        encrypted = self.settings["llm"].get(f"{provider}_api_key_encrypted")
        if encrypted:
            decrypted = self.decrypt_value(encrypted)
            if decrypted:
                logger.debug(f"Using {provider} API key from encrypted config")
                return decrypted

        # 3. Try plain text key (for manual config)
        return self.settings["llm"].get(f"{provider}_api_key")

    def get(self, key_path: str, default=None) -> Any:
        """
        Get a setting value using dot notation.
        """
        keys = key_path.split('.')
        value = self.settings

        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default

    def set(self, key_path: str, value: Any):
        """
        Set a setting value using dot notation.
        """
        keys = key_path.split('.')
        target = self.settings

        for key in keys[:-1]:
            if key not in target:
                target[key] = {}
            target = target[key]

        target[keys[-1]] = value
        self.save()