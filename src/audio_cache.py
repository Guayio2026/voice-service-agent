"""Bounded PCM cache, disk-backed when a root is given.

Authorization, identity and citation checks stay in the caller and run on every
lookup, including hits: this module only stores audio bytes keyed by an exact
(voice, identity, spoken text, evidence) tuple. Keys never contain a session id,
so the same line spoken in a later call reuses the same asset.
"""
import hashlib
import json
import os
import time
from collections import OrderedDict
from pathlib import Path


class AudioCache:
    def __init__(self, root=None, max_bytes=None, ttl=30 * 86400):
        self.root = Path(root) if root is not None else None
        self.max_bytes = max_bytes if max_bytes is not None else (200_000_000 if self.root else 8_640_000)
        self.ttl, self.size = ttl, 0
        self.entries = OrderedDict()          # name -> (created, size, audio_or_None)
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            for path in self.root.glob('*.pcm'):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if time.time() - stat.st_mtime >= self.ttl:
                    path.unlink(missing_ok=True)
                    continue
                self.entries[path.stem] = (stat.st_mtime, stat.st_size, None)
                self.size += stat.st_size
            self._evict()

    @staticmethod
    def name(key):
        raw = key if isinstance(key, str) else json.dumps(list(key), ensure_ascii=False)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]

    def _now(self):
        # Disk entries age with wall clock (matches file mtime); memory entries
        # keep the monotonic clock so expiry is unaffected by system time changes.
        return time.time() if self.root is not None else time.monotonic()

    def _path(self, name):
        return self.root / (name + '.pcm')

    def _drop(self, name, entry):
        self.size -= entry[1]
        if self.root is not None:
            try:
                self._path(name).unlink(missing_ok=True)
            except OSError:
                pass

    def _evict(self):
        while self.entries and self.size > self.max_bytes:
            name, entry = self.entries.popitem(last=False)
            self._drop(name, entry)

    def get(self, key):
        name = self.name(key)
        entry = self.entries.pop(name, None)
        if entry is None:
            return None
        created, size, audio = entry
        if self._now() - created >= self.ttl:
            self._drop(name, entry)
            return None
        if self.root is not None:
            try:
                audio = self._path(name).read_bytes()
            except OSError:
                self._drop(name, entry)
                return None
            size = len(audio)
        elif audio is None:
            return None
        self.entries[name] = (created, size, audio)
        return audio

    def put(self, key, audio):
        if not audio or len(audio) > self.max_bytes:
            return
        name = self.name(key)
        previous = self.entries.pop(name, None)
        if previous:
            self.size -= previous[1]
        if self.root is not None:
            path = self._path(name)
            temp = self.root / ('.' + name + '.tmp')
            try:
                temp.write_bytes(audio)
                os.replace(temp, path)
            except OSError:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
                return
            self.entries[name] = (self._now(), len(audio), None)
        else:
            self.entries[name] = (self._now(), len(audio), audio)
        self.size += len(audio)
        self._evict()
