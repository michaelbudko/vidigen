"""Song rights attestation.

Vidigen cannot verify copyright on its own, so every render requires the user
to state the song's licence. Licences that require credit must include it, and
the credit is written into the video metadata and the suggested caption.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, model_validator


class SongLicense(str, Enum):
    public_domain = "public_domain"
    cc0 = "cc0"
    cc_by = "cc_by"
    cc_by_sa = "cc_by_sa"
    own_work = "own_work"
    licensed = "licensed"  # the user holds a sync/commercial licence for it


LABELS = {
    SongLicense.public_domain: "Public domain",
    SongLicense.cc0: "CC0",
    SongLicense.cc_by: "CC BY 4.0",
    SongLicense.cc_by_sa: "CC BY-SA 4.0",
    SongLicense.own_work: "Original work",
    SongLicense.licensed: "Licensed",
}

NEEDS_CREDIT = {SongLicense.cc_by, SongLicense.cc_by_sa}


class SongRights(BaseModel):
    license: SongLicense
    title: str = ""
    artist: str = ""
    source_url: str = ""
    attested: bool = False

    @model_validator(mode="after")
    def _check(self) -> "SongRights":
        if not self.attested:
            raise ValueError("confirm you have the right to use this song")
        if self.license in NEEDS_CREDIT and not (self.title and self.artist):
            raise ValueError(f"{LABELS[self.license]} requires the song title and artist for attribution")
        return self

    def credit_line(self) -> str:
        if not (self.title or self.artist):
            return ""
        who = f'"{self.title}" by {self.artist}' if self.title and self.artist else (self.title or self.artist)
        parts = [f"Music: {who}", LABELS[self.license]]
        if self.source_url:
            parts.append(self.source_url)
        return " · ".join(parts)
