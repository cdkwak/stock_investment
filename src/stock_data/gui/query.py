from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


@dataclass
class LocalParquetQuery:
    """Small, observable reads over partitioned local Parquet artifacts."""

    root: Path
    files_read: list[Path] = field(default_factory=list)
    _frame_cache: OrderedDict[
        tuple[Path, tuple[str, ...] | None, tuple[tuple[str, tuple[str, ...]], ...]],
        tuple[int, int, pd.DataFrame],
    ] = field(default_factory=OrderedDict, init=False, repr=False)
    _small_file_cache: OrderedDict[
        Path, tuple[int, int, pd.DataFrame],
    ] = field(default_factory=OrderedDict, init=False, repr=False)
    _tail_cache: OrderedDict[
        tuple[object, ...],
        tuple[tuple[tuple[Path, int, int], ...], tuple[Path, ...], pd.DataFrame],
    ] = field(default_factory=OrderedDict, init=False, repr=False)

    _FRAME_CACHE_LIMIT = 32
    _SMALL_FILE_CACHE_LIMIT = 16
    _SMALL_FILE_CACHE_BYTES = 128 * 1024
    _TAIL_CACHE_LIMIT = 16

    @staticmethod
    def _timestamp(value: object) -> pd.Timestamp:
        """Parse retained local wall-clock labels without deprecated KST guessing."""

        if isinstance(value, str) and value.rstrip().endswith(" KST"):
            value = value.rstrip()[:-4]
        return pd.Timestamp(value)

    @staticmethod
    def _path_signature(paths: tuple[Path, ...]) -> tuple[tuple[Path, int, int], ...] | None:
        signature = []
        try:
            for path in paths:
                stat = path.stat()
                signature.append((path, stat.st_mtime_ns, stat.st_size))
        except OSError:
            return None
        return tuple(signature)

    def _tail_cache_get(
        self, key: tuple[object, ...],
    ) -> pd.DataFrame | None:
        cached = self._tail_cache.get(key)
        cache_key = key
        if cached is None:
            requested_rows = int(key[1])
            for candidate_key, candidate in reversed(self._tail_cache.items()):
                if (
                    int(candidate_key[1]) >= requested_rows
                    and candidate_key[0] == key[0]
                    and candidate_key[2:] == key[2:]
                ):
                    cache_key = candidate_key
                    cached = candidate
                    break
        if cached is None:
            return None
        signature, files, frame = cached
        current = self._path_signature(tuple(item[0] for item in signature))
        if current != signature:
            del self._tail_cache[cache_key]
            return None
        self._tail_cache.move_to_end(cache_key)
        self.files_read.extend(files)
        return frame.tail(int(key[1])).reset_index(drop=True).copy(deep=True)

    def _tail_cache_put(
        self,
        key: tuple[object, ...],
        *,
        base: Path,
        files: tuple[Path, ...],
        frame: pd.DataFrame,
    ) -> None:
        watched = {base}
        for path in files:
            watched.update((path, path.parent, path.parent.parent))
        watched_paths = tuple(sorted(watched))
        signature = self._path_signature(watched_paths)
        if signature is None:
            return
        self._tail_cache[key] = (signature, files, frame.copy(deep=True))
        self._tail_cache.move_to_end(key)
        while len(self._tail_cache) > self._TAIL_CACHE_LIMIT:
            self._tail_cache.popitem(last=False)

    def _read_frame(
        self,
        path: Path,
        *,
        columns: list[str] | None,
        filters: dict[str, tuple[str, ...]] | None,
    ) -> pd.DataFrame:
        normalized_filters = tuple(sorted(
            (key, tuple(values)) for key, values in (filters or {}).items()
        ))
        key = (path, tuple(columns) if columns is not None else None, normalized_filters)
        stat = path.stat()
        if stat.st_size <= self._SMALL_FILE_CACHE_BYTES:
            small = self._small_file_cache.get(path)
            if small is None or small[:2] != (stat.st_mtime_ns, stat.st_size):
                full = pd.read_parquet(path)
                self._small_file_cache[path] = (
                    stat.st_mtime_ns, stat.st_size, full.copy(deep=True),
                )
                self._small_file_cache.move_to_end(path)
                while len(self._small_file_cache) > self._SMALL_FILE_CACHE_LIMIT:
                    self._small_file_cache.popitem(last=False)
            else:
                self._small_file_cache.move_to_end(path)
                full = small[2].copy(deep=True)
            if all(name in full for name, _values in normalized_filters) and (
                columns is None or all(column in full for column in columns)
            ):
                for name, values in normalized_filters:
                    full = full[full[name].isin(values)]
                frame = full if columns is None else full.loc[:, columns]
                self.files_read.append(path)
                return frame.reset_index(drop=True)

        cached = self._frame_cache.get(key)
        if cached is not None and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            self._frame_cache.move_to_end(key)
            self.files_read.append(path)
            return cached[2].copy(deep=True)

        predicates = [
            (name, "in", list(values)) for name, values in normalized_filters
        ]
        frame = pd.read_parquet(
            path, columns=columns, filters=predicates or None,
        )
        self._frame_cache[key] = (
            stat.st_mtime_ns, stat.st_size, frame.copy(deep=True),
        )
        self._frame_cache.move_to_end(key)
        while len(self._frame_cache) > self._FRAME_CACHE_LIMIT:
            self._frame_cache.popitem(last=False)
        self.files_read.append(path)
        return frame

    @staticmethod
    def _exact_partition_root(
        base: Path, partitions: dict[str, str] | None,
    ) -> Path | None:
        """Return an exact safe direct Hive partition, otherwise ``None``."""

        requested = tuple((partitions or {}).items())
        if not requested or not base.exists():
            return None
        candidate = base
        base_resolved = base.resolve()
        for key, value in requested:
            token = f"{key}={value}"
            if Path(token).name != token:
                return None
            direct = candidate / token
            try:
                if (
                    not direct.is_dir()
                    or not direct.resolve().is_relative_to(base_resolved)
                ):
                    return None
            except OSError:
                return None
            candidate = direct
        return candidate

    @classmethod
    def _parquet_paths(
        cls, base: Path, partitions: dict[str, str] | None,
    ) -> list[Path]:
        """Enumerate an exact direct Hive partition before the dataset root.

        Any missing, non-directory, nested-layout, or unsafe requested token
        falls back to the prior root enumeration and exact path filtering.
        """

        if not base.exists():
            return []
        requested = tuple((partitions or {}).items())
        candidate = cls._exact_partition_root(base, partitions)
        if candidate is not None:
            return list(candidate.rglob("*.parquet"))

        paths = list(base.rglob("*.parquet"))
        for key, value in requested:
            token = f"{key}={value}"
            paths = [path for path in paths if token in path.parts]
        return paths

    @classmethod
    def _direct_year_roots(
        cls, base: Path, partitions: dict[str, str] | None,
    ) -> tuple[dict[int, list[Path]], list[Path]] | None:
        """Describe flat year roots, optionally below one Hive partition level."""

        if not base.exists():
            return {}, []
        requested = tuple((partitions or {}).items())
        root = cls._exact_partition_root(base, partitions)
        if requested:
            return None if root is None else cls._flat_year_roots((root,))

        direct = cls._flat_year_roots((base,))
        if direct is not None:
            return direct
        try:
            children = tuple(base.iterdir())
        except OSError:
            return None
        partition_roots = tuple(
            child for child in children
            if child.is_dir() and "=" in child.name
            and not child.name.startswith("year=")
        )
        if not partition_roots or len(partition_roots) != sum(
            child.is_dir() for child in children
        ):
            return None
        return cls._flat_year_roots(partition_roots)

    @staticmethod
    def _flat_year_roots(
        roots: tuple[Path, ...],
    ) -> tuple[dict[int, list[Path]], list[Path]] | None:
        years: dict[int, list[Path]] = {}
        direct_files: list[Path] = []
        for root in roots:
            try:
                children = tuple(root.iterdir())
            except OSError:
                return None
            for child in children:
                if child.is_file():
                    if child.suffix.lower() == ".parquet":
                        direct_files.append(child)
                    continue
                if not child.is_dir():
                    continue
                name = child.name
                if not name.startswith("year=") or not name[5:].isdigit():
                    return None
                years.setdefault(int(name[5:]), []).append(child)
        return years, sorted(direct_files)

    @classmethod
    def _fast_year_roots(
        cls,
        base: Path,
        partitions: dict[str, str] | None,
        years: tuple[int, ...],
    ) -> dict[int, list[Path]] | None:
        """Construct recent year paths without listing historical year folders."""

        requested = tuple((partitions or {}).items())
        exact = cls._exact_partition_root(base, partitions)
        if requested:
            if exact is None:
                return None
            parents = (exact,)
        elif any((base / f"year={year}").is_dir() for year in years):
            parents = (base,)
        else:
            try:
                children = tuple(base.iterdir())
            except OSError:
                return None
            directories = tuple(child for child in children if child.is_dir())
            if (
                not directories
                or len(directories) != len(children)
                or any(
                    "=" not in child.name or child.name.startswith("year=")
                    for child in directories
                )
            ):
                return None
            parents = directories
        result: dict[int, list[Path]] = {}
        for year in years:
            roots = [
                parent / f"year={year}" for parent in parents
                if (parent / f"year={year}").is_dir()
            ]
            if roots:
                result[year] = roots
        return result or None

    def read(
        self,
        dataset: str,
        *,
        start: object | None = None,
        end: object | None = None,
        columns: list[str] | None = None,
        partitions: dict[str, str] | None = None,
        filters: dict[str, tuple[str, ...]] | None = None,
    ) -> pd.DataFrame:
        base = self.root / dataset
        paths = self._parquet_paths(base, partitions)
        if start is not None or end is not None:
            lo = self._timestamp(start).year if start is not None else -9999
            hi = self._timestamp(end).year if end is not None else 9999
            year_paths = [p for p in paths if any(
                part.startswith("year=") and part[5:].isdigit() and lo <= int(part[5:]) <= hi
                for part in p.parts
            )]
            if year_paths:
                paths = year_paths
        frames = []
        for path in sorted(paths):
            try:
                frame = self._read_frame(
                    path, columns=columns, filters=filters,
                )
            except (OSError, PermissionError, ValueError):
                # A retained artifact can be unreadable or have an older schema.
                # Keep the GUI read-only and render the affected section as
                # unavailable instead of turning one file into a dashboard crash.
                continue
            frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=columns or [])
        result = pd.concat(frames, ignore_index=True)
        date_column = "date" if "date" in result else "market_date" if "market_date" in result else None
        if date_column:
            result[date_column] = pd.to_datetime(result[date_column], errors="coerce")
            if start is not None:
                result = result[result[date_column] >= self._timestamp(start)]
            if end is not None:
                result = result[result[date_column] <= self._timestamp(end)]
        return result.reset_index(drop=True)

    def tail(
        self,
        dataset: str,
        *,
        rows: int,
        columns: list[str],
        end: object | None = None,
        partitions: dict[str, str] | None = None,
        filters: dict[str, tuple[str, ...]] | None = None,
    ) -> pd.DataFrame:
        """Read newest year partitions through ``end`` until ``rows`` are available."""
        base = self.root / dataset
        end_timestamp = self._timestamp(end) if end is not None else None
        normalized_partitions = tuple(sorted((partitions or {}).items()))
        normalized_filters = tuple(sorted(
            (key, tuple(values)) for key, values in (filters or {}).items()
        ))
        cache_key = (
            dataset, int(rows), tuple(columns),
            end_timestamp.isoformat() if end_timestamp is not None else None,
            normalized_partitions, normalized_filters,
        )
        cached = self._tail_cache_get(cache_key)
        if cached is not None:
            return cached
        files_read_start = len(self.files_read)
        fast_years: dict[int, list[Path]] | None = None
        if end_timestamp is not None:
            # Most market datasets contain roughly 200+ observations per year.
            # Probe only the recent years needed for this request before listing
            # every retained historical partition. Sparse data still falls back
            # to full discovery below when the probe does not yield enough rows.
            year_window = max(2, (max(rows, 1) + 199) // 200 + 1)
            years = tuple(end_timestamp.year - offset for offset in range(year_window))
            fast_years = self._fast_year_roots(base, partitions, years)
        direct_years = None if fast_years is not None else self._direct_year_roots(
            base, partitions,
        )
        by_year: dict[int, list[Path]] = {}
        if fast_years is not None:
            by_year.update(fast_years)
        elif direct_years is None:
            paths = self._parquet_paths(base, partitions)
            for path in paths:
                year = next((int(p[5:]) for p in path.parts if p.startswith("year=") and p[5:].isdigit()), -1)
                if end_timestamp is not None and year > end_timestamp.year:
                    continue
                by_year.setdefault(year, []).append(path)
        else:
            year_roots, direct_files = direct_years
            if direct_files:
                by_year[-1] = direct_files
            for year, roots in year_roots.items():
                if end_timestamp is not None and year > end_timestamp.year:
                    continue
                by_year[year] = roots
        frames: list[pd.DataFrame] = []
        count = 0
        for year in sorted(by_year, reverse=True):
            candidates = by_year[year]
            if year >= 0 and (
                fast_years is not None or direct_years is not None
            ):
                candidates = sorted(
                    path
                    for root in candidates
                    for path in root.rglob("*.parquet")
                )
            for path in sorted(candidates):
                try:
                    frame = self._read_frame(
                        path, columns=columns, filters=filters,
                    )
                except (OSError, PermissionError, ValueError):
                    continue
                date_column = "date" if "date" in frame else "market_date"
                frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")
                if end_timestamp is not None:
                    frame = frame[frame[date_column] <= end_timestamp]
                frames.append(frame)
                count += len(frame)
            if count >= rows:
                break
        if count < rows and fast_years is not None:
            direct_years = self._direct_year_roots(base, partitions)
            fallback_years: dict[int, list[Path]] = {}
            if direct_years is None:
                for path in self._parquet_paths(base, partitions):
                    year = next((
                        int(part[5:]) for part in path.parts
                        if part.startswith("year=") and part[5:].isdigit()
                    ), -1)
                    if year not in by_year and year <= end_timestamp.year:
                        fallback_years.setdefault(year, []).append(path)
            else:
                year_roots, direct_files = direct_years
                if direct_files and -1 not in by_year:
                    fallback_years[-1] = direct_files
                for year, roots in year_roots.items():
                    if year not in by_year and year <= end_timestamp.year:
                        fallback_years[year] = roots
            for year in sorted(fallback_years, reverse=True):
                candidates = fallback_years[year]
                if year >= 0 and direct_years is not None:
                    candidates = sorted(
                        path for root in candidates
                        for path in root.rglob("*.parquet")
                    )
                for path in sorted(candidates):
                    try:
                        frame = self._read_frame(
                            path, columns=columns, filters=filters,
                        )
                    except (OSError, PermissionError, ValueError):
                        continue
                    date_column = "date" if "date" in frame else "market_date"
                    frame[date_column] = pd.to_datetime(
                        frame[date_column], errors="coerce",
                    )
                    frame = frame[frame[date_column] <= end_timestamp]
                    frames.append(frame)
                    count += len(frame)
                if count >= rows:
                    break
        if not frames:
            result = pd.DataFrame(columns=columns)
        else:
            result = pd.concat(frames, ignore_index=True)
            date_column = "date" if "date" in result else "market_date"
            result[date_column] = pd.to_datetime(result[date_column], errors="coerce")
            if end_timestamp is not None:
                result = result[result[date_column] <= end_timestamp]
            result = result.sort_values(date_column).tail(rows).reset_index(drop=True)
        logical_files = tuple(self.files_read[files_read_start:])
        self._tail_cache_put(
            cache_key, base=base, files=logical_files, frame=result,
        )
        return result
