"""Parts compatibility drafting — CPU <-> Motherboard, by socket/chipset family.

This module IS the "AI drafts compatibility" step described in POC_BUILD_PLAN
§4/§5. Socket/chipset compatibility is standardized public engineering
knowledge (not a business decision), so it is encoded here as an explicit,
reviewable rule table rather than a live model call — same effect (a drafted
judgment with confidence, subject to human confirmation), fully auditable and
reproducible run to run. A live LLM call could later replace/extend this
table for parts not covered here (e.g. obscure custom parts); the contract
(confidence + origin + draft status) stays the same either way.

Every classifier returns (family, confidence). confidence='high' only for
well-established, unambiguous mappings; 'medium' for pattern-inferred but
less certain cases (Xeon/Celeron/Pentium, old APUs); items that don't match
any known pattern return (None, 'low') and must route to human review.
"""
from __future__ import annotations

import re

# -- Intel Core generation -> socket family --------------------------------
INTEL_GEN_SOCKET = {
    4: "LGA1150", 5: "LGA1150",
    6: "LGA1151v1", 7: "LGA1151v1",
    8: "LGA1151v2", 9: "LGA1151v2",
    10: "LGA1200", 11: "LGA1200",
    12: "LGA1700", 13: "LGA1700", 14: "LGA1700",
}

# HEDT ("X-series") desktop CPUs reuse mainstream-looking model numbers but
# sit on a DIFFERENT socket (LGA2011-v3 / LGA2066) — a generic digit-prefix
# rule would misclassify these, so they are listed explicitly (verified
# against this catalogue's exact SKU set, not inferred).
INTEL_HEDT_OVERRIDE = {
    "i75820K": "LGA2011v3", "i75930K": "LGA2011v3", "i75960X": "LGA2011v3",
    "i76950X": "LGA2011v3",
    "i77800X": "LGA2066", "i77820X": "LGA2066",
    "i97900X": "LGA2066", "i97920X": "LGA2066", "i97940X": "LGA2066",
    "i97960X": "LGA2066", "i97980XE": "LGA2066",
    "i910900X": "LGA2066", "i910920X": "LGA2066", "i910940X": "LGA2066",
    "i910980XE": "LGA2066",
}

# Celeron/Pentium "G" desktop SKUs verified against this catalogue's exact
# model list (era identified from the public Intel G-series numbering).
CELERON_PENTIUM_G_MAP = {
    "cG1820": "LGA1150", "cG1840": "LGA1150",          # Haswell (gen4 era)
    "cG3900": "LGA1151v1", "cG3930": "LGA1151v1",       # Skylake (gen6 era)
    "cG4900": "LGA1151v2", "cG4930": "LGA1151v2",       # Coffee Lake (gen8 era)
    "G6900": "LGA1700",                                  # Alder Lake (gen12 era)
}

# -- Motherboard chipset -> socket family (verified from ONE MB item names) --
CHIPSET_SOCKET = {
    # Intel LGA1151v1 (6th/7th gen)
    "H110": "LGA1151v1", "B150": "LGA1151v1", "B250": "LGA1151v1",
    "H170": "LGA1151v1", "Q170": "LGA1151v1", "Z170": "LGA1151v1",
    "Z270": "LGA1151v1", "H270": "LGA1151v1", "Q270": "LGA1151v1",
    # Intel LGA1151v2 (8th/9th gen)
    "B360": "LGA1151v2", "B365": "LGA1151v2", "H310": "LGA1151v2",
    "H370": "LGA1151v2", "Q370": "LGA1151v2", "Z370": "LGA1151v2",
    "Z390": "LGA1151v2", "C242": "LGA1151v2", "C246": "LGA1151v2",
    # Intel LGA1150 (Xeon E3 v3/v4, Haswell/Broadwell desktop) — server/legacy
    "H81": "LGA1150", "B85": "LGA1150", "H97": "LGA1150",
    "Q87": "LGA1150", "C224": "LGA1150", "C232": "LGA1151v1",
    # Intel LGA1200 (10th/11th gen)
    "B460": "LGA1200", "H410": "LGA1200", "Q470": "LGA1200",
    "Z490": "LGA1200", "H470": "LGA1200", "B560": "LGA1200",
    "H510": "LGA1200", "Q570": "LGA1200", "Z590": "LGA1200", "H570": "LGA1200",
    # Intel LGA1150 (4th/5th gen desktop, Haswell/Broadwell) — legacy chipset
    "Z97": "LGA1150",
    # Intel LGA1700 (12th/13th/14th gen)
    "B660": "LGA1700", "H610": "LGA1700", "Q670": "LGA1700",
    "Z690": "LGA1700", "B760": "LGA1700", "H770": "LGA1700",
    "Q770": "LGA1700", "Z790": "LGA1700", "H670": "LGA1700",
    # Intel LGA1851 (Core Ultra 200S "Arrow Lake-S") — per parts roadmap PPT
    "B860": "LGA1851", "H810": "LGA1851", "Q870": "LGA1851",
    "W880": "LGA1851", "Z890": "LGA1851",
    # Intel HEDT
    "X299": "LGA2066", "X99": "LGA2011v3",
    # AMD AM3+ (FX)
    "970A": "AM3+", "990FX": "AM3+",
    # AMD FM2+ (A-series APUs)
    "A68": "FM2+", "A78": "FM2+", "A88": "FM2+",
    # AMD AM4 (Ryzen 1000-5000 + Athlon/Ryzen-G APUs)
    "A320": "AM4", "A520": "AM4", "B350": "AM4", "B450": "AM4",
    "B550": "AM4", "X370": "AM4", "X470": "AM4", "X570": "AM4",
    # AMD AM5 (Ryzen 7000-9000 + Ryzen-G APUs)
    "A620": "AM5", "B650": "AM5", "B840": "AM5", "B850": "AM5",
    "X670": "AM5", "X870": "AM5",
    # AMD Threadripper
    "TRX40": "sTRX4", "TRX50": "sTR5", "WRX90": "sWRX9",
}

_INTEL_CORE_RE = re.compile(r"^i([3579])(\d{2,3})(\d{2,3})([A-Z]*)$")
_INTEL_ULTRA_RE = re.compile(r"^U[579]-\d")     # Core Ultra 200S "Arrow Lake-S"
_MOBILE_SUFFIX_RE = re.compile(r"(M|MQ|U|H|HQ|C)$")   # laptop-only SKUs
_XEON_RE = re.compile(r"^x", re.IGNORECASE)
_CELERON_PENTIUM_RE = re.compile(r"^[cp]", re.IGNORECASE)
_RYZEN_RE = re.compile(r"^[Rr]y(\d)(\d)(\d{3})")
_ATHLON_RE = re.compile(r"^At", re.IGNORECASE)
_A_SERIES_RE = re.compile(r"^A(\d)")   # AMD A-series FM2+ APU, e.g. A107700K
_FX_RE = re.compile(r"^FX")


def classify_cpu_socket(name: str) -> tuple[str | None, str]:
    """Intel iN-XXXXX -> generation from the numeric block; AMD RyN -> series."""
    n = name.strip()
    if n in INTEL_HEDT_OVERRIDE:
        return INTEL_HEDT_OVERRIDE[n], "high"
    if n in CELERON_PENTIUM_G_MAP:
        return CELERON_PENTIUM_G_MAP[n], "high"
    if _INTEL_ULTRA_RE.match(n):
        return "LGA1851", "high"
    m = _INTEL_CORE_RE.match(n)
    if m:
        if _MOBILE_SUFFIX_RE.search(m.group(4) or "") or m.group(4) in ("MQ",):
            return None, "low"          # laptop CPU — no desktop socket
        digits = m.group(2) + m.group(3)
        gen = int(digits[:2]) if len(digits) >= 5 else int(digits[0])
        socket = INTEL_GEN_SOCKET.get(gen)
        return (socket, "high") if socket else (None, "low")
    m = _RYZEN_RE.match(n)
    if m:
        series = int(m.group(1))
        # Ryzen 1000/2000/3000/5000(non-3D uses same AM4)/4000G/5000G -> AM4
        # Ryzen 7000/8000G/9000 -> AM5
        if series in (1, 2, 3, 4, 5):
            return "AM4", "high"
        if series in (7, 8, 9):
            return "AM5", "high"
        return None, "low"
    if _FX_RE.match(n):
        return "AM3+", "high"
    if _ATHLON_RE.match(n):
        return "AM4", "medium"           # Athlon 200GE/3000G are AM4
    m = _A_SERIES_RE.match(n)
    if m:
        return "FM2+", "medium"          # AMD A-series APU (older FM2+ era)
    if _XEON_RE.match(n):
        return "LGA1150", "medium"       # Xeon E3 v3/v4 in this catalogue — needs review
    if _CELERON_PENTIUM_RE.match(n):
        return None, "low"               # shares era sockets with Core but ambiguous here
    return None, "low"


def classify_mb_socket(name: str) -> tuple[str | None, str]:
    """Match the chipset code embedded in the board name against CHIPSET_SOCKET."""
    n = name.strip().upper()
    # longest-prefix match against known chipset codes (handles suffixes like
    # 'H170M', 'Z390' embedded in 'STRIX Z390', 'B860 LiveMixer', etc.)
    best = None
    for code, socket in CHIPSET_SOCKET.items():
        if code in n and (best is None or len(code) > len(best[0])):
            best = (code, socket)
    if best:
        return best[1], "high"
    return None, "low"


def draft_cpu_mb_rules(cpu_items: list, mb_items: list) -> list[dict]:
    """cpu_items/mb_items: objects with .id and .canonical_name.

    Returns draft compat_rules rows: {part_a_id, part_b_id, relation,
    confidence, origin, cpu_socket, mb_socket} — one per (cpu, mb) pair that
    shares a socket family. Cross-vendor / unmatched items are simply not
    paired (absence of a rule, not a negative rule).
    """
    cpu_by_socket: dict[str, list] = {}
    cpu_conf: dict[int, str] = {}
    for item in cpu_items:
        socket, conf = classify_cpu_socket(item.canonical_name)
        if socket:
            cpu_by_socket.setdefault(socket, []).append(item)
            cpu_conf[item.id] = conf

    mb_by_socket: dict[str, list] = {}
    mb_conf: dict[int, str] = {}
    for item in mb_items:
        socket, conf = classify_mb_socket(item.canonical_name)
        if socket:
            mb_by_socket.setdefault(socket, []).append(item)
            mb_conf[item.id] = conf

    conf_rank = {"high": 3, "medium": 2, "low": 1}
    rules = []
    for socket, cpus in cpu_by_socket.items():
        for mb in mb_by_socket.get(socket, []):
            for cpu in cpus:
                pair_conf = min(cpu_conf[cpu.id], mb_conf[mb.id],
                                key=lambda c: conf_rank[c])
                rules.append({"part_a_id": cpu.id, "part_b_id": mb.id,
                             "relation": "fits", "confidence":
                             {"high": 0.95, "medium": 0.7, "low": 0.4}[pair_conf],
                             "origin": "ai_spec_heuristic", "socket": socket})
    return rules


def unclassified_report(cpu_items: list, mb_items: list) -> dict:
    """Items with no socket match at all — need_review, not silently dropped."""
    cpu_unk = [i.canonical_name for i in cpu_items
              if classify_cpu_socket(i.canonical_name)[0] is None]
    mb_unk = [i.canonical_name for i in mb_items
             if classify_mb_socket(i.canonical_name)[0] is None]
    return {"cpu_unclassified": cpu_unk, "mb_unclassified": mb_unk}
