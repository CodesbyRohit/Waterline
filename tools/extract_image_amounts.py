#!/usr/bin/env python3
"""
Extract amounts for blank-amount financial events by OCR-ing the linked receipt
images (dataset/media/images/<image_id>.png, linked via dataset/images.csv).

This is a one-time extraction tool: its verified output is committed as
engine/image_amounts.json so the main pipeline stays deterministic and does not
need an OCR dependency at run time.

Usage:
    pip install rapidocr-onnxruntime pillow
    python tools/extract_image_amounts.py
"""

import csv
import json
import os
import re
import sys

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    sys.exit("Missing dependency: pip install rapidocr-onnxruntime")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_rows(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# Hand-verified values (from reading each receipt) take precedence where the
# best-effort heuristics below disagree. Mapped as {event_id: value}.
MANUAL_OVERRIDES = {
    # image_02: rent receipt — "Balance Due: 1,00,000.00" (total 2,00,000,
    # received 1,00,000; the event is the outstanding balance)
    "event_1442": 100000,
    # image_14: pharmacy bill — OCR reads the total as "443o0" (letter o),
    # so the numeric parse fails; total is Rs 443.00
    "event_9421": 443.0,
    # image_16: EV charging — words parse gives 393; exact total is 393.22
    "event_10521": 393.22,
}

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"thousand": 10**3, "lakh": 10**5, "lac": 10**5,
           "million": 10**6, "crore": 10**7, "billion": 10**9}


def _words_to_number(text):
    """Parse an amount-in-words line, e.g. 'Rupees Seven Hundred Twenty Three
    Only' or 'Four Million Three Hundred SixtyFive Thousand Rupiahs'."""
    t = text.lower()
    # drop the trailing paise/paisa phrase: cut from the last ' and ' before
    # the paise word (e.g. 'Rupees and Five Paise', 'Rupees And Zero Paisa',
    # 'Rupee Seventy-Nine Thousand ... and Twenty-Six Paise')
    m = re.search(r"\bpais[ae]\b", t)
    if m:
        before = t[:m.start()]
        a = before.rfind(" and ")
        if a != -1:
            before = before[:a]
        t = before + " " + t[m.end():]
    t = re.sub(r"[^a-z\s]", " ", t)
    t = re.sub(r"\b(and|only|rupees|rupee|rupiahs|rupiah|rs)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    total, current, seen = 0, 0, False
    for w in t.split():
        if w in _ONES:
            current += _ONES[w]
            seen = True
        elif w in _TENS:
            current += _TENS[w]
            seen = True
        elif w in _SCALES:
            current = max(current, 1) * _SCALES[w]
            total += current
            current = 0
            seen = True
        elif w.endswith("hundred"):
            current = max(current, 1) * 100
            seen = True
        # hyphenless compounds like "sixtyfive" are joined words
        elif seen and w.isalpha():
            m = re.match(r"(" + "|".join(sorted(_TENS, key=len, reverse=True)) + r")(\w*)$", w)
            if m and m.group(2) in _ONES:
                current += _TENS[m.group(1)] + _ONES[m.group(2)]
    total += current
    return total if seen and total > 0 else None


def extract_amount_from_lines(lines, event_currency, home_currency):
    """Pull the total amount out of OCR'd receipt lines.

    Strategy, in order:
      1. Explicit totals:  net pay, net amount, grand total, total amount received,
         total paid, amount due till, "total".
      2. Fall back to the largest currency-annotated number on the receipt.
    """
    currency = re.escape(event_currency or home_currency or "")

    def nums(text):
        out = []
        for m in re.finditer(r"(\d{1,3}(?:[,\d]{0,15})(?:\.\d{1,2})?)", text):
            tok = m.group(1)
            try:
                v = float(tok.replace(",", ""))
            except ValueError:
                continue
            # skip long digit runs with no decimal point: phones, GSTIN/FSSAI,
            # receipt / transaction ids, HSN concatenations
            if "." not in tok and len(tok.replace(",", "")) >= 8:
                continue
            if v > 0:
                out.append(v)
        return out

    # 0. amount-in-words lines are the most reliable total signal
    for line in lines:
        low = line.lower()
        if ("rupee" in low or "rupiah" in low) and (
                "only" in low or "thousand" in low or "million" in low
                or "lakh" in low or "hundred" in low):
            v = _words_to_number(line)
            if v:
                # refine with the exact numeric total (e.g. 'Total' then
                # '79,679.26' on the next OCR line) when a matching rupee-part
                # exists near a total line
                for idx, ln in enumerate(lines):
                    low2 = ln.lower()
                    if re.search(r"\btotal\b", low2) and not re.search(r"sub\s*total", low2):
                        nearby = nums(ln)
                        for nxt in lines[idx + 1: idx + 3]:
                            if re.search(r"\btotal\b", nxt.lower()) and "rupee" not in nxt.lower():
                                break
                            nearby.extend(nums(nxt))
                        for cand in nearby:
                            if v <= cand < v + 1:
                                return cand
                return float(v)

    # 1. explicit total lines, scanned in priority order (case-insensitive)
    pattern_groups = [
        [r"grand\s*total"],
        [r"total\s*amount\s*received", r"total\s*paid", r"net\s*pay",
         r"net\s*amount"],
        [r"total\s*bill\s*amount", r"amount\s*due\s*till"],
        [r"\btotal\b"],
    ]
    for group in pattern_groups:
        for i, line in enumerate(lines):
            low = line.lower()
            if re.search(r"sub\s*total", low):
                continue
            if any(re.search(p, low) for p in group):
                candidates = nums(line)
                # the number may be on the same OCR box or the next ones
                j = i
                while not candidates and j + 1 < len(lines):
                    j += 1
                    candidates = nums(lines[j])
                if candidates:
                    # prefer the last number on a "Total" line
                    if re.search(r"\btotal\b", low):
                        return candidates[-1]
                    return candidates[0]

    # 2. largest currency-annotated number anywhere
    vals = []
    for line in lines:
        if currency and re.search(currency, line, re.IGNORECASE):
            vals.extend(nums(line))
    if vals:
        return max(vals)

    # 3. largest number anywhere (last resort)
    all_vals = [v for line in lines for v in nums(line)]
    return max(all_vals) if all_vals else None


def main():
    images = load_rows(os.path.join(ROOT, "dataset", "images.csv"))
    events = {r["event_id"]: r for r in load_rows(
        os.path.join(ROOT, "dataset", "financial_events.csv"))}
    profiles = {r["user_id"]: r for r in load_rows(
        os.path.join(ROOT, "dataset", "financial_profiles.csv"))}

    blank = [e for e in events.values() if not e["amount"].strip()]
    print(f"{len(blank)} events with blank amount")

    ocr = RapidOCR()
    out = {}
    for ev in blank:
        if ev["event_id"] in MANUAL_OVERRIDES:
            out[ev["event_id"]] = float(MANUAL_OVERRIDES[ev["event_id"]])
            print(f"  {ev['event_id']} ({ev['user_id']}, {ev['category']}): "
                  f"{out[ev['event_id']]} (manual override)")
            continue
        linked = [i for i in images if i["related_event_id"] == ev["event_id"]]
        if not linked:
            print(f"  !! no image linked for {ev['event_id']}")
            continue
        img_id = linked[0]["image_id"]
        path = os.path.join(ROOT, "dataset", "media", "images", f"{img_id}.png")
        if not os.path.exists(path):
            print(f"  !! missing image {path}")
            continue
        res, _ = ocr(path)
        lines = [r[1] for r in res] if res else []
        home = profiles[ev["user_id"]]["home_currency"]
        amt = extract_amount_from_lines(lines, ev["currency"], home)
        if amt is None:
            print(f"  !! could not extract amount for {ev['event_id']} from {img_id}")
            continue
        out[ev["event_id"]] = round(amt, 2)
        print(f"  {ev['event_id']} ({img_id}, {ev['user_id']}, {ev['category']}): {amt}")

    json.dump(out, open(os.path.join(ROOT, "engine", "image_amounts.json"), "w",
                        encoding="utf-8"), indent=1)
    print(f"wrote {len(out)} amounts to engine/image_amounts.json")


if __name__ == "__main__":
    main()
