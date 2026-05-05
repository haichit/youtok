import re
import unicodedata


INVALID = '<>:"/\\|?*\'`[](){}+,;!@#$%^&='


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    for c in INVALID:
        s = s.replace(c, "")
    s = re.sub(r"\s+", "-", s).strip("-").lower()
    return s[:80]


def make_folder_name(title: str, video_id: str) -> str:
    return f"{slug(title)}_{video_id}"
