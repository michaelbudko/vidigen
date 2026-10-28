"""Song licence info, used to credit the song in the caption and metadata.

Vidigen cannot verify copyright, and nothing here blocks a render. When the
licence needs attribution (CC BY / BY-SA) the UI reminds the user to add it.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


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
    license: SongLicense = SongLicense.own_work
    title: str = ""
    artist: str = ""
    source_url: str = ""

    @property
    def missing_credit(self) -> bool:
        return self.license in NEEDS_CREDIT and not (self.title and self.artist)

    def credit_line(self) -> str:
        if not (self.title or self.artist):
            return ""
        who = f'"{self.title}" by {self.artist}' if self.title and self.artist else (self.title or self.artist)
        parts = [f"Music: {who}", LABELS[self.license]]
        if self.source_url:
            parts.append(self.source_url)
        return " · ".join(parts)
