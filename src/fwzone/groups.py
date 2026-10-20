"""Group 定義：載入、循環檢查、遞迴展開（SPEC 3.3、第 4 節）。只使用純 Python 資料結構。"""

from collections.abc import Collection, Iterable
from typing import NamedTuple

from fwzone.errors import InputError
from fwzone.parsing import (
    DEFAULT_ANY_KEYWORDS,
    DEFAULT_DELIMITERS,
    ParsedToken,
    TokenKind,
    TokenParser,
    tokenize,
)


class ExpandedMember(NamedTuple):
    """Group 展開後的一個成員。

    via 為展開路徑，由最外層 group 起、最內層 group 止（直接寫在規則中的 token 不在此列）。
    """

    token: ParsedToken
    via: tuple[str, ...]


class GroupResolver:
    """載入 group 定義，檢查循環參照，並展開成員。

    rows 為 (group 名稱, 成員儲存格內容)。同一 group 可出現在多列，成員合併。
    Group 名稱比對為大小寫敏感、去除前後空白的完全相等。
    """

    def __init__(
        self,
        rows: Iterable[tuple[str, str]],
        delimiters: Collection[str] = DEFAULT_DELIMITERS,
        any_keywords: Iterable[str] = DEFAULT_ANY_KEYWORDS,
    ) -> None:
        raw_members: dict[str, list[str]] = {}
        for name, cell in rows:
            name = name.strip()
            if not name:
                # TODO(spec): group 名稱為空的列未定義處理方式，目前略過
                continue
            raw_members.setdefault(name, []).extend(tokenize(cell, delimiters))

        self._names = frozenset(raw_members)
        self.parser = TokenParser(self.__contains__, any_keywords)
        self._members: dict[str, list[ParsedToken]] = {}
        for name, tokens in raw_members.items():
            parsed = (self.parser.parse(t) for t in tokens)
            self._members[name] = [p for p in parsed if p is not None]
        self._cache: dict[str, tuple[ExpandedMember, ...]] = {}
        self._check_cycles()

    def __contains__(self, name: object) -> bool:
        return name in self._names

    def __len__(self) -> int:
        return len(self._names)

    def expand(self, name: str) -> tuple[ExpandedMember, ...]:
        """遞迴展開 group 成員；同一 group 只展開一次。name 必須是已定義的 group。"""
        cached = self._cache.get(name)
        if cached is not None:
            return cached

        result: list[ExpandedMember] = []
        seen: set[ExpandedMember] = set()
        for token in self._members[name]:
            if token.kind is TokenKind.GROUP:
                items = [ExpandedMember(m.token, (name, *m.via)) for m in self.expand(token.raw)]
            else:
                items = [ExpandedMember(token, (name,))]
            for item in items:
                if item not in seen:
                    seen.add(item)
                    result.append(item)

        expanded = tuple(result)
        self._cache[name] = expanded
        return expanded

    def _check_cycles(self) -> None:
        """載入時檢查循環參照（如 G1 -> G2 -> G1），發現即拋出 InputError 並列出路徑。"""
        children = {
            name: [t.raw for t in tokens if t.kind is TokenKind.GROUP]
            for name, tokens in self._members.items()
        }
        done: set[str] = set()

        for start in sorted(children):
            if start in done:
                continue
            path: list[str] = []
            on_path: set[str] = set()
            # 迭代式 DFS：堆疊內為 (節點, 下一個要走的子節點索引)
            stack: list[tuple[str, int]] = [(start, 0)]
            path.append(start)
            on_path.add(start)
            while stack:
                node, i = stack.pop()
                if i < len(children[node]):
                    stack.append((node, i + 1))
                    child = children[node][i]
                    if child in on_path:
                        cycle = [*path[path.index(child) :], child]
                        raise InputError("group 循環參照：" + " -> ".join(cycle))
                    if child not in done:
                        path.append(child)
                        on_path.add(child)
                        stack.append((child, 0))
                else:
                    done.add(node)
                    on_path.discard(node)
                    path.pop()
