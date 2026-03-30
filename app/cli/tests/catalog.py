from __future__ import annotations

import shlex
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TestRequirement:
    env_vars: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def summary(self) -> str:
        parts: list[str] = []
        if self.env_vars:
            parts.append("env:" + ",".join(self.env_vars))
        parts.extend(self.notes)
        return " | ".join(parts)


@dataclass(frozen=True)
class TestCatalogItem:
    id: str
    kind: str
    display_name: str
    description: str
    command: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    source_path: str = ""
    requirements: TestRequirement = field(default_factory=TestRequirement)
    children: tuple[TestCatalogItem, ...] = ()

    @property
    def command_display(self) -> str:
        return shlex.join(self.command) if self.command else ""

    @property
    def is_runnable(self) -> bool:
        return bool(self.command)

    def matches(self, *, category: str = "all", search: str = "") -> bool:
        if category != "all" and category not in self.tags:
            return False
        if not search:
            return True

        query = search.lower()
        searchable = " ".join(
            [
                self.id,
                self.display_name,
                self.description,
                " ".join(self.tags),
                self.source_path,
            ]
        ).lower()
        return query in searchable


def iter_items(items: tuple[TestCatalogItem, ...]) -> list[TestCatalogItem]:
    flattened: list[TestCatalogItem] = []
    for item in items:
        flattened.append(item)
        if item.children:
            flattened.extend(iter_items(item.children))
    return flattened


@dataclass(frozen=True)
class TestCatalog:
    items: tuple[TestCatalogItem, ...]

    def all_items(self) -> list[TestCatalogItem]:
        return iter_items(self.items)

    def find(self, item_id: str) -> TestCatalogItem | None:
        for item in self.all_items():
            if item.id == item_id:
                return item
        return None

    def filter(self, *, category: str = "all", search: str = "") -> list[TestCatalogItem]:
        matched: list[TestCatalogItem] = []
        for item in self.items:
            if item.matches(category=category, search=search):
                matched.append(item)
                continue
            if any(child.matches(category=category, search=search) for child in item.children):
                matched.append(item)
        return matched
