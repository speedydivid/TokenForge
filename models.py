"""Pydantic models for the API and storage layer."""
from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .fingerprint import Fingerprint, generate as generate_fingerprint


ProxyType = Literal["http", "https", "socks5"]
ProxyMode = Literal["manual", "random", "rotate"]
# Which browser engine a profile launches with.
#   camoufox  — patched Firefox, native-level fingerprint spoofing (strongest stealth)
#   chromium  — Playwright Chromium, best site compatibility + true mobile emulation
#   android   — real Android Emulator (AVD): a genuine Chrome-for-Android engine, not
#               a spoof. Requires the one-click Android SDK install (see android.py).
Engine = Literal["camoufox", "chromium", "android"]


class Proxy(BaseModel):
    type: ProxyType = "http"
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""

    @property
    def is_set(self) -> bool:
        return bool(self.host and self.port)

    def server_url(self) -> str:
        """Return a proxy URL as Camoufox/Playwright expects (`scheme://host:port`)."""
        scheme = "socks5" if self.type == "socks5" else "http"
        return f"{scheme}://{self.host}:{self.port}"

    def playwright_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"server": self.server_url()}
        if self.username:
            d["username"] = self.username
        if self.password:
            d["password"] = self.password
        return d


class FingerprintModel(BaseModel):
    os: str = "windows"
    # Mobile (Android) emulation; defaults keep desktop/older stored profiles valid.
    is_mobile: bool = False
    device_name: str = ""
    user_agent: str = ""
    app_version: str = ""
    platform: str = ""
    oscpu: str = ""
    screen_width: int = 1920
    screen_height: int = 1080
    webgl_vendor: str = ""
    webgl_renderer: str = ""
    hardware_concurrency: int = 8
    device_memory: int = 8
    language: str = "en-US"
    region: str = "US"
    timezone: str = "America/New_York"
    # deep randomization vectors (defaults keep older stored profiles valid)
    color_depth: int = 24
    device_pixel_ratio: float = 1.0
    max_touch_points: int = 0
    do_not_track: str = "unspecified"
    audio_sample_rate: int = 48000
    audio_channels: int = 2
    canvas_aa_offset: int = 0
    canvas_aa_cap_offset: bool = True
    fonts: list[str] = Field(default_factory=list)
    fonts_spacing_seed: int = 0
    battery_charging: bool = True
    battery_level: float = 1.0
    battery_charging_time: float = 0.0
    battery_discharging_time: float = 0.0
    webcams: int = 0
    micros: int = 1
    speakers: int = 1
    webrtc_local_ipv4: str = ""

    def to_fingerprint(self) -> Fingerprint:
        return Fingerprint.from_dict(self.model_dump())

    @classmethod
    def from_fingerprint(cls, fp: Fingerprint) -> "FingerprintModel":
        return cls(**fp.to_dict())


class Profile(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    start_url: str = "about:blank"
    engine: Engine = "camoufox"    # browser engine to launch (see Engine above)
    proxy: Proxy = Field(default_factory=Proxy)
    # Proxy selection: "manual" uses `proxy`; "random"/"rotate" pick from `proxy_pool`
    # at launch (random draw, or round-robin advancing `rotation_index`).
    proxy_mode: ProxyMode = "manual"
    proxy_pool: list[Proxy] = Field(default_factory=list)
    rotation_index: int = 0
    fingerprint: FingerprintModel = Field(default_factory=FingerprintModel)
    # Behavioural / hardening toggles handed to Camoufox.
    humanize: bool = True          # human-like cursor movement
    block_webrtc: bool = True      # prevent WebRTC IP leaks
    geoip: bool = True             # match locale/timezone to the proxy's exit IP
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def select_proxy(self) -> tuple[Proxy, Optional[int]]:
        """Resolve the proxy to launch with, plus the next rotation index (or None).

        - manual: the single configured proxy; no index change.
        - random: a random pool entry; no index change.
        - rotate: the pool entry at `rotation_index`, and the advanced index to
          persist so the next launch uses the following proxy.
        Empty pools fall back to the manual proxy.
        """
        import random as _random

        pool = [p for p in self.proxy_pool if p.is_set]
        if self.proxy_mode == "random" and pool:
            return _random.choice(pool), None
        if self.proxy_mode == "rotate" and pool:
            idx = self.rotation_index % len(pool)
            return pool[idx], (idx + 1) % len(pool)
        return self.proxy, None


class ProfileCreate(BaseModel):
    name: str
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    start_url: str = "about:blank"
    engine: Engine = "camoufox"
    proxy: Optional[Proxy] = None
    proxy_mode: ProxyMode = "manual"
    proxy_pool: list[Proxy] = Field(default_factory=list)
    os: Optional[str] = None            # constrain generated fingerprint to this OS
    device: Optional[str] = None        # pin a specific phone model (see list_mobile_devices)
    fingerprint: Optional[FingerprintModel] = None  # or supply one fully
    humanize: bool = True
    block_webrtc: bool = True
    geoip: bool = True

    def build(self) -> Profile:
        fp = (
            self.fingerprint.to_fingerprint()
            if self.fingerprint
            else generate_fingerprint(os_name=self.os, device=self.device)
        )
        return Profile(
            name=self.name,
            notes=self.notes,
            tags=self.tags,
            start_url=self.start_url,
            engine=self.engine,
            proxy=self.proxy or Proxy(),
            proxy_mode=self.proxy_mode,
            proxy_pool=self.proxy_pool,
            fingerprint=FingerprintModel.from_fingerprint(fp),
            humanize=self.humanize,
            block_webrtc=self.block_webrtc,
            geoip=self.geoip,
        )


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[list[str]] = None
    start_url: Optional[str] = None
    engine: Optional[Engine] = None
    proxy: Optional[Proxy] = None
    proxy_mode: Optional[ProxyMode] = None
    proxy_pool: Optional[list[Proxy]] = None
    rotation_index: Optional[int] = None
    fingerprint: Optional[FingerprintModel] = None
    humanize: Optional[bool] = None
    block_webrtc: Optional[bool] = None
    geoip: Optional[bool] = None
