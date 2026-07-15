"""LLM-assisted compatibility-rule drafting for parts the deterministic
spec table (engine/compat.py) cannot classify — the "new product" gap.

engine/compat.py's own docstring reserves exactly this seam: "A live LLM
call could later replace/extend this table for parts not covered here …
the contract (confidence + origin + draft status) stays the same."

Guardrails (BR-010 — AI proposes, a human decides):
  * Only items the pattern table returns (None, 'low') for are sent to the
    LLM — everything the table already knows stays deterministic.
  * The LLM may only pick from socket families the table already knows
    (plus "unknown"); an invented family is treated as unknown, never stored.
  * Confidence is the model's own raw 0.0-1.0 self-assessment, stored as
    reported — NOT capped below the review queue's bulk-confirm threshold
    (changed 2026-07-10, by explicit request, after confirming the tradeoff:
    LLMs are known to be poorly self-calibrated — a wrong answer can report
    high confidence just as easily as a right one, e.g. the W790 WS
    misclassification during testing came back "high" — so a confidently
    wrong LLM draft CAN now be swept in by bulk-confirm without individual
    review. Every LLM-origin row is still clearly origin='llm' with the
    model's stated reasoning in `note`, so it's visible to a reviewer who
    looks; bulk-confirm just no longer forces that look).
  * origin='llm' and the model's stated reasoning are stored on the rule,
    so the reviewer sees WHY the model thinks the part fits that family.
  * Manual-trigger only, degrades gracefully: unreachable model server means
    a reported skip, never an exception into the review workflow.

The model server is in-house (config.LLM_URL, OpenAI-compatible) — part
roadmaps are 社外秘, so classification requests never leave the network.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from ..config import LLM_API_KEY, LLM_MODEL, LLM_TIMEOUT, LLM_URL
from ..engine.compat import (CHIPSET_SOCKET, INTEL_GEN_SOCKET,
                             INTEL_HEDT_OVERRIDE, classify_cpu_socket,
                             classify_mb_socket)
from ..models import CompatRule, Item

BATCH_SIZE = 25


def known_families() -> list[str]:
    fams = set(CHIPSET_SOCKET.values()) | set(INTEL_GEN_SOCKET.values())
    fams |= set(INTEL_HEDT_OVERRIDE.values())
    return sorted(fams)


def _default_transport(payload: dict) -> str:
    """POST an OpenAI-style chat payload to the local server, return the
    assistant message content. Swapped out in tests."""
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    req = urllib.request.Request(
        f"{LLM_URL.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        body = json.loads(resp.read().decode())
    return body["choices"][0]["message"]["content"]


def _prompt(names: list[str], kind: str, families: list[str]) -> str:
    part_desc = ("desktop CPU model short-codes" if kind == "cpu"
                 else "PC motherboard product short-codes")
    return (
        f"You are a PC-hardware compatibility expert. The following are {part_desc} "
        f"from a Japanese BTO PC maker's internal catalogue (short aliases, e.g. "
        f"'U7-265K', 'B860M Pro RS'; some contain Japanese text).\n\n"
        f"For EACH name, identify its CPU socket family. You MUST choose from "
        f"exactly this list (these are the only families the downstream system "
        f"accepts):\n{', '.join(families)}\n\n"
        f"Rules:\n"
        f"- If the part is a laptop/mobile CPU (no desktop socket), or you are "
        f"not confident, use \"unknown\".\n"
        f"- First identify the part's REAL socket/chipset from your own "
        f"knowledge (e.g. \"LGA1155\" or \"LGA4677\"), THEN check whether "
        f"that exact name appears in the allowed list below. If it does not "
        f"appear — even if a similar-looking or nearby-generation family is "
        f"in the list — you MUST answer \"unknown\". Do NOT substitute the "
        f"closest or most-similar-sounding family from the list; a wrong "
        f"answer that looks plausible is worse than admitting you don't "
        f"know. Example: if the true socket is LGA1155 and only LGA1150 is "
        f"in the list, answer \"unknown\", not \"LGA1150\".\n"
        f"- confidence: a NUMBER from 0.0 to 1.0 for how certain you are. "
        f"1.0 = verified public engineering fact (e.g. a well-documented "
        f"chipset/socket pairing); 0.5 = a reasonable inference from "
        f"partial evidence; near 0.0 = a weak guess. Use the full range — "
        f"do not default to round numbers like 0.5 or 0.9 out of habit; "
        f"actually estimate how likely you are to be right.\n"
        f"- reasoning: name the SPECIFIC evidence (the exact chipset code, or "
        f"the CPU microarchitecture/generation you recognized) — not a vague "
        f"restatement of the family name.\n\n"
        f"Respond with ONLY a JSON array, no other text:\n"
        f'[{{"name": "...", "family": "...", "confidence": 0.0-1.0, '
        f'"reasoning": "..."}}]\n\n'
        f"Names:\n" + "\n".join(f"- {n}" for n in names)
    )


def _extract_json_array(text: str) -> list:
    """Tolerant parse: take the first [...] block in the response (models
    sometimes wrap JSON in prose or code fences despite instructions)."""
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON array in LLM response")
    return json.loads(m.group(0))


def classify_with_llm(names: list[str], kind: str,
                      transport=None) -> tuple[dict[str, dict], list[str]]:
    """→ ({name: {family, confidence, reasoning}}, unknown_names).
    Only names classified into a KNOWN family with a valid numeric
    confidence appear in the dict; everything else lands in unknown_names.
    confidence is the model's raw 0.0-1.0 self-report, stored as-is — see
    the module docstring for why this is no longer capped."""
    transport = transport or _default_transport
    families = known_families()
    classified: dict[str, dict] = {}
    unknown: list[str] = []
    for i in range(0, len(names), BATCH_SIZE):
        batch = names[i:i + BATCH_SIZE]
        content = transport({
            "model": LLM_MODEL,
            "messages": [{"role": "user",
                          "content": _prompt(batch, kind, families)}],
            "temperature": 0,
        })
        try:
            rows = _extract_json_array(content)
        except (ValueError, json.JSONDecodeError):
            unknown.extend(batch)          # unparseable batch → all to review
            continue
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            if name not in batch or name in seen:
                continue                   # hallucinated or duplicated name
            seen.add(name)
            family = str(row.get("family", "")).strip()
            try:
                conf = float(row.get("confidence"))
            except (TypeError, ValueError):
                conf = None
            valid_conf = conf is not None and 0.0 <= conf <= 1.0
            if family in families and valid_conf:
                classified[name] = {"family": family, "confidence": conf,
                                    "reasoning": str(row.get("reasoning", ""))[:300]}
            else:
                unknown.append(name)
        unknown.extend(n for n in batch if n not in seen and n not in unknown)
    return classified, unknown


def draft_llm_rules(session, transport=None) -> dict:
    """Full pipeline: unclassified items → LLM family proposal → cross-pair
    against every part already in that family → store as origin='llm' drafts.

    Prior origin='llm' DRAFTS are replaced on re-run (idempotent);
    human-confirmed/rejected LLM rules are never touched — those are
    decisions, not drafts."""
    cpu_items = session.query(Item).filter(Item.category == "CPU").all()
    mb_items = session.query(Item).filter(Item.category == "ﾏｻﾞｰﾎﾞｰﾄﾞ").all()

    # split: engine-classified members (pair targets) vs unclassified (LLM input)
    cpu_family: dict[str, list[Item]] = {}
    cpu_unk: list[Item] = []
    for it in cpu_items:
        fam, _ = classify_cpu_socket(it.canonical_name)
        (cpu_family.setdefault(fam, []).append(it) if fam else cpu_unk.append(it))
    mb_family: dict[str, list[Item]] = {}
    mb_unk: list[Item] = []
    for it in mb_items:
        fam, _ = classify_mb_socket(it.canonical_name)
        (mb_family.setdefault(fam, []).append(it) if fam else mb_unk.append(it))

    result = {"ok": True, "model": LLM_MODEL,
              "cpu_unclassified": len(cpu_unk), "mb_unclassified": len(mb_unk),
              "classified": 0, "needs_review": 0,
              "rules_drafted": 0, "skipped_existing": 0, "error": None}
    if not cpu_unk and not mb_unk:
        return result

    by_name = {it.canonical_name: it for it in cpu_unk + mb_unk}
    try:
        cpu_cls, cpu_rest = classify_with_llm(
            [i.canonical_name for i in cpu_unk], "cpu", transport)
        mb_cls, mb_rest = classify_with_llm(
            [i.canonical_name for i in mb_unk], "mb", transport)
    except (urllib.error.URLError, OSError, KeyError) as e:
        result["ok"] = False
        result["error"] = (f"LLM server unreachable or bad response "
                           f"({LLM_URL}): {e}")
        return result

    result["classified"] = len(cpu_cls) + len(mb_cls)
    result["needs_review"] = len(cpu_rest) + len(mb_rest)

    # replace previous LLM DRAFTS only; keep human-decided rows
    session.query(CompatRule).filter(CompatRule.origin == "llm",
                                     CompatRule.status == "draft").delete()
    session.flush()
    existing = {(r.part_a_id, r.part_b_id, r.relation)
                for r in session.query(CompatRule).all()}

    def _store(cpu: Item, mb: Item, confidence: float, reasoning: str,
               llm_part: str) -> None:
        key = (cpu.id, mb.id, "fits")
        if key in existing:
            result["skipped_existing"] += 1
            return
        existing.add(key)
        session.add(CompatRule(
            part_a_id=cpu.id, part_b_id=mb.id, relation="fits",
            confidence=confidence, origin="llm", status="draft",
            note=f"[{llm_part}] {reasoning}"))
        result["rules_drafted"] += 1

    # LLM-classified CPUs × engine-known MBs of that family
    for name, cls in cpu_cls.items():
        cpu = by_name[name]
        for mb in mb_family.get(cls["family"], []):
            _store(cpu, mb, cls["confidence"], cls["reasoning"], "cpu")
    # LLM-classified MBs × engine-known CPUs, and × LLM-classified CPUs
    for name, cls in mb_cls.items():
        mb = by_name[name]
        for cpu in cpu_family.get(cls["family"], []):
            _store(cpu, mb, cls["confidence"], cls["reasoning"], "mb")
        for cname, ccls in cpu_cls.items():
            if ccls["family"] == cls["family"]:
                weakest = min(cls["confidence"], ccls["confidence"])
                _store(by_name[cname], mb, weakest,
                       f"cpu: {ccls['reasoning']} / mb: {cls['reasoning']}",
                       "both")
    session.commit()
    return result
