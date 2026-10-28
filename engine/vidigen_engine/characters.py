"""Characters: a named person or mascot built from reference photos.

Each character keeps its uploaded photos, a face crop of each (what the face
adapter conditions on), a text description, revision notes, a likeness
strength, and the preview portraits generated while tuning it. A preview the
user pins as "this is the look" is also used as an extra reference.
"""
from __future__ import annotations

import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps
from pydantic import BaseModel, Field

FACE_SIZE = 512

STYLES = {
    "realistic": "photorealistic portrait photo, natural light, 85mm lens, detailed skin",
    "cartoon": "cartoon character, bold clean outlines, flat vibrant colors, expressive",
    "anime": "anime character, cel shading, clean line art",
    "3d": "3D animated movie character, soft studio lighting, stylized",
    "meme": "funny caricature, exaggerated expression, bold colors, meme style",
}

try:  # iPhone photos
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass


class Photo(BaseModel):
    file: str
    face: str
    enabled: bool = True


class Preview(BaseModel):
    file: str
    seed: int
    prompt: str
    style: str
    batch: int
    created: float = Field(default_factory=time.time)


class Character(BaseModel):
    id: str
    name: str
    description: str = ""
    notes: list[str] = Field(default_factory=list)  # revisions, e.g. "hair a bit shorter"
    likeness: float = Field(default=0.7, ge=0.0, le=1.0)
    style: str = "realistic"
    photos: list[Photo] = Field(default_factory=list)
    previews: list[Preview] = Field(default_factory=list)
    look: Optional[str] = None  # pinned preview file
    look_as_reference: bool = True
    consent: bool = False
    created: float = Field(default_factory=time.time)
    updated: float = Field(default_factory=time.time)

    def appearance(self) -> str:
        parts = [self.description.strip(), *[n.strip() for n in self.notes if n.strip()]]
        return ", ".join(p for p in parts if p) or "a person"

    def portrait_prompt(self, style: str | None = None) -> str:
        s = STYLES.get(style or self.style, STYLES["realistic"])
        return f"portrait of {self.appearance()}, head and shoulders, looking at the camera, {s}"


class CharacterPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[list[str]] = None
    likeness: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    style: Optional[str] = None
    look: Optional[str] = None
    clear_look: bool = False
    look_as_reference: Optional[bool] = None
    photo_enabled: Optional[dict[str, bool]] = None


def face_crop(src: Path, out: Path) -> Path:
    """Square crop around the largest face, padded to include hair and chin.
    Falls back to the upper-centre of the photo when no face detector is available."""
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    w, h = img.size
    box = None
    try:
        import cv2
        import numpy as np

        gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
        det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = det.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(max(40, w // 20),) * 2)
        if len(faces):
            x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            cx, cy, side = x + fw / 2, y + fh / 2 - fh * 0.1, max(fw, fh) * 1.9
            box = (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)
    except Exception:
        box = None
    if box is None:
        side = min(w, h)
        cx, cy = w / 2, min(h - side / 2, max(side / 2, h * 0.4))
        box = (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)
    # Clamp the square inside the image.
    side = min(box[2] - box[0], w, h)
    left = min(max(0, box[0]), w - side)
    top = min(max(0, box[1]), h - side)
    crop = img.crop((int(left), int(top), int(left + side), int(top + side))).resize((FACE_SIZE, FACE_SIZE))
    out.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out)
    return out


class CharacterStore:
    def __init__(self, root: Path):
        self.root = root

    def dir(self, cid: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]+", cid):
            raise KeyError(cid)
        return self.root / cid

    def _save(self, c: Character) -> Character:
        c.updated = time.time()
        d = self.dir(c.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "character.json").write_text(c.model_dump_json(indent=2), encoding="utf-8")
        return c

    def list(self) -> list[Character]:
        if not self.root.exists():
            return []
        out = []
        for f in self.root.glob("*/character.json"):
            try:
                out.append(Character.model_validate_json(f.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return sorted(out, key=lambda c: c.created)

    def get(self, cid: str) -> Character:
        f = self.dir(cid) / "character.json"
        if not f.is_file():
            raise KeyError(cid)
        return Character.model_validate_json(f.read_text(encoding="utf-8"))

    def create(self, name: str, consent: bool, photos: list[str], description: str = "", style: str = "realistic") -> Character:
        if not consent:
            raise ValueError("confirm these photos are of you, or that you have the person's permission")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "character"
        c = Character(id=f"{slug}-{uuid.uuid4().hex[:6]}", name=name.strip() or "Me", consent=True,
                      description=description, style=style)
        self._save(c)
        return self.add_photos(c.id, photos) if photos else c

    def add_photos(self, cid: str, paths: list[str]) -> Character:
        c = self.get(cid)
        d = self.dir(cid)
        for p in paths:
            src = Path(p)
            if not src.is_file():
                raise FileNotFoundError(p)
            stem = uuid.uuid4().hex[:8]
            dest = d / "photos" / f"{stem}{src.suffix.lower()}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            try:
                face = face_crop(dest, d / "faces" / f"{stem}.png")
            except Exception as e:
                dest.unlink(missing_ok=True)
                raise ValueError(f"could not read {src.name} as an image: {e}") from e
            c.photos.append(Photo(file=dest.relative_to(d).as_posix(), face=face.relative_to(d).as_posix()))
        return self._save(c)

    def remove_photo(self, cid: str, file: str) -> Character:
        c = self.get(cid)
        d = self.dir(cid)
        for ph in [p for p in c.photos if p.file == file]:
            (d / ph.file).unlink(missing_ok=True)
            (d / ph.face).unlink(missing_ok=True)
        c.photos = [p for p in c.photos if p.file != file]
        return self._save(c)

    def update(self, cid: str, patch: CharacterPatch) -> Character:
        c = self.get(cid)
        for k in ("name", "description", "notes", "likeness", "style", "look_as_reference"):
            v = getattr(patch, k)
            if v is not None:
                setattr(c, k, v)
        if patch.clear_look:
            c.look = None
        elif patch.look is not None:
            if patch.look not in {p.file for p in c.previews}:
                raise ValueError("unknown preview")
            c.look = patch.look
        for file, on in (patch.photo_enabled or {}).items():
            for p in c.photos:
                if p.file == file:
                    p.enabled = on
        return self._save(c)

    def delete(self, cid: str) -> None:
        shutil.rmtree(self.dir(cid), ignore_errors=True)

    def references(self, c: Character) -> list[Path]:
        d = self.dir(c.id)
        refs = [d / p.face for p in c.photos if p.enabled and (d / p.face).is_file()]
        if c.look and c.look_as_reference:
            look_face = d / "faces" / f"look-{Path(c.look).stem}.png"
            if not look_face.is_file() and (d / c.look).is_file():
                face_crop(d / c.look, look_face)
            if look_face.is_file():
                refs.append(look_face)
        return refs

    def render_previews(self, cid: str, backend, count: int = 4, style: str | None = None, seed: int | None = None,
                        progress=None) -> Character:
        """Generate `count` candidate portraits with the character's current settings."""
        c = self.get(cid)
        refs = self.references(c)
        if not refs:
            raise ValueError("add at least one photo first")
        batch = max((p.batch for p in c.previews), default=0) + 1
        style = style or c.style
        prompt = c.portrait_prompt(style)
        base = seed if seed is not None else int(time.time()) % 100000
        d = self.dir(cid)
        for i in range(count):
            if progress:
                progress(i, count)
            out = d / "previews" / f"b{batch:03d}-{i}.png"
            backend.generate(prompt, base + i, out, refs=refs, likeness=c.likeness, square=True)
            c.previews.append(Preview(file=out.relative_to(d).as_posix(), seed=base + i, prompt=prompt,
                                      style=style, batch=batch))
        # Keep the editor light: only the latest 6 batches, plus the pinned look.
        keep = {p.batch for p in c.previews if p.batch > batch - 6}
        for p in c.previews:
            if p.batch not in keep and p.file != c.look:
                (d / p.file).unlink(missing_ok=True)
        c.previews = [p for p in c.previews if p.batch in keep or p.file == c.look]
        return self._save(c)
