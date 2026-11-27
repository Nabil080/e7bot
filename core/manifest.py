"""Loads templates/manifest.yaml, the list of every template the state machine expects."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from core.config import ConfigError, State, StrictModel


class TemplateEntry(StrictModel):
    """A UI element the bot looks for. Its image is <name>.png in the templates folder."""

    name: str
    state: State | None = None
    description: str = ""
    region: tuple[int, int, int, int] | None = None


class Manifest(StrictModel):

    templates: list[TemplateEntry]

    def names(self) -> list[str]:
        """Return every template name, in file order."""
        return [entry.name for entry in self.templates]

    def missing(self, templates_dir: Path) -> list[str]:
        """Return the names of templates that have no image yet."""
        return [name for name in self.names() if not (templates_dir / f"{name}.png").is_file()]

    def set_region(self, name: str, region: tuple[int, int, int, int]) -> None:
        """Record where a template was cropped in game space, adding the entry if it is new."""
        for entry in self.templates:
            if entry.name == name:
                entry.region = region
                return
        self.templates.append(TemplateEntry(name=name, region=region))


def load_manifest(path: Path) -> Manifest:
    """Load and validate a manifest, raising ConfigError with a readable message."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        manifest = Manifest.model_validate(raw)
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    except (yaml.YAMLError, ValidationError) as error:
        raise ConfigError(f"{path} is invalid: {error}") from error
    duplicates = {name for name in manifest.names() if manifest.names().count(name) > 1}
    if duplicates:
        raise ConfigError(f"{path} has duplicate templates: {sorted(duplicates)}")
    return manifest


def save_manifest(manifest: Manifest, path: Path) -> None:
    """Write a manifest back to disk."""
    data = manifest.model_dump(mode="json", exclude_defaults=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
