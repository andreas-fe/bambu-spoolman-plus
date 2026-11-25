"""Pure, conservative 3MF/AMS/stock preflight. Never starts a print."""
from __future__ import annotations
import math
import re
from collections import defaultdict


def material(value):
    return re.sub(r"\s+", " ", str(value or "").strip().upper())


def color(value):
    v = str(value or "").strip().lstrip("#").upper()
    return v[:6] if re.fullmatch(r"[0-9A-F]{6}(?:[0-9A-F]{2})?", v) else None


def weight(value):
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) and v >= 0 else None
    except (TypeError, ValueError):
        return None


def exact_match(filament, material_value, color_value):
    return bool(material(filament.get("material"))) and (
        material(filament["material"]) == material(material_value)
        and color(filament.get("color")) is not None
        and color(filament["color"]) == color(color_value)
    )


def review_plate(metadata, plate_index, context, mapping=None):
    """Read-only assessment; never an authorization to start or switch hardware."""
    if type(plate_index) is not int or not 1 <= plate_index <= 64:
        raise ValueError("Gültige Modellplatte wählen.")
    if not isinstance(metadata, dict) or not isinstance(context, dict):
        raise ValueError("Druckdaten fehlen.")
    plate = next((p for p in metadata.get("plates", []) if p.get("index") == plate_index), None)
    if not plate or not plate.get("filaments") or len(plate["filaments"]) > 16:
        raise ValueError("Keine eindeutigen geslicten Filamentdaten.")
    if mapping is None:
        mapping = {}
    if not isinstance(mapping, dict):
        raise ValueError("Ungültige Schachtzuordnung.")
    ids = {str(f["id"]) for f in plate["filaments"]}
    if any(str(k) not in ids or not isinstance(v, str) or len(v) > 20 for k, v in mapping.items()):
        raise ValueError("Unbekannte Filamentposition oder Schachtzuordnung.")
    issues, recommendations, lines = [], [], []
    slots = context.get("slots") or []
    stock = context.get("spools") or []
    by_id = {str(s.get("id")): s for s in stock}
    needed_by_spool = defaultdict(float)
    selected_by_spool = defaultdict(set)

    def problem(level, message):
        issues.append({"level": level, "message": message})

    if plate.get("outside"):
        problem("blocked", "Objekte laut 3MF außerhalb des Druckbereichs.")
    if material(metadata.get("model")) != material(context.get("model")):
        problem("blocked", "Druckermodell der geslicten Datei passt nicht.")
    try:
        if float(plate.get("nozzle")) != float(context.get("nozzle")):
            problem("blocked", "Düsengröße der Datei passt nicht.")
    except (ValueError, TypeError):
        problem("review", "Düsengröße ist nicht bestätigt.")
    if not context.get("connected") or context.get("state") == "OFFLINE":
        problem("review", "Drucker offline; AMS-Daten nicht bestätigt.")
    elif context.get("state") in ("RUNNING", "PREPARE", "PAUSE"):
        problem("review", "Drucker ist aktiv; diese Prüfung ist keine Startfreigabe.")
    if not context.get("inventory_available", True):
        problem("review", "Bestandsdaten nicht zuverlässig erreichbar.")

    for f in plate["filaments"]:
        fid = str(f["id"])
        desc = "Filament " + fid + " (" + str(f.get("material") or "?") + " " + str(f.get("color") or "?") + ")"
        needed = weight(f.get("grams"))
        if needed is None or needed <= 0:
            problem("review", desc + ": Verbrauchsmenge unbekannt.")
        matches = [
            t for t in slots if t.get("available") and t.get("spool_id") is not None
            and exact_match(f, t.get("material"), t.get("color"))
            and exact_match(f, t.get("spool_material"), t.get("spool_color"))
        ]
        selected = mapping.get(fid, "")
        chosen = next((t for t in slots if t.get("slot") == selected), None) if selected else None
        suggested = matches[0] if len(matches) == 1 else None
        active = chosen or suggested
        if selected and chosen is None:
            problem("blocked", desc + ": ausgewählter AMS-Schacht existiert nicht.")
        elif chosen is not None and chosen not in matches:
            problem("blocked", desc + ": Slot passt nicht exakt zu Material, Farbe und physischer Bestandsrolle.")
        elif active is None:
            problem("review", desc + (": mehrere passende AMS-Schächte; bitte auswählen." if matches else ": keine bestätigte passende AMS-Rolle."))
        remaining = None
        if active in matches:
            sid = str(active["spool_id"])
            selected_by_spool[sid].add(active["slot"])
            remaining = weight(by_id.get(sid, {}).get("remaining_weight"))
            if remaining is None:
                problem("review", desc + ": Restgewicht unbekannt.")
            if needed is not None:
                needed_by_spool[sid] += needed
        if not matches:
            candidates = []
            for s in stock:
                if s.get("archived") or not exact_match(f, s.get("material"), s.get("clean_color")):
                    continue
                available = weight(s.get("remaining_weight"))
                if needed is not None and available is not None and available >= needed:
                    candidates.append({"spool_id": s.get("id"), "remaining_g": available})
            if candidates:
                recommendations.append({"action": "check_spool_swap", "filament_id": f["id"],
                                        "message": desc + ": kompatible Lagerrolle physisch einlegen und AMS anschließend prüfen.",
                                        "candidates": candidates[:5]})
        lines.append({"id": f["id"], "material": f.get("material"), "color": f.get("color"),
                      "needed_g": needed, "selected_slot": selected or None,
                      "suggested_slot": suggested.get("slot") if suggested else None,
                      "matching_slots": [t["slot"] for t in matches],
                      "spool_id": active.get("spool_id") if active in matches else None,
                      "remaining_g": remaining})

    for sid, total in needed_by_spool.items():
        remaining = weight(by_id.get(sid, {}).get("remaining_weight"))
        if remaining is not None and round(total, 2) > remaining + 1e-9:
            problem("blocked", f"Bestandsrolle #{sid}: {total:.2f} g erforderlich, nur {remaining:g} g vorhanden.")
        if len(selected_by_spool[sid]) > 1:
            problem("review", f"Bestandsrolle #{sid} mehrfach in unterschiedlichen AMS-Schächten zugeordnet.")

    selected_external = sum(1 for x in lines if x["selected_slot"] == "external")
    if selected_external and len(lines) != 1:
        problem("blocked", "Externe Rolle kann nicht mit mehreren Slicer-Filamenten gemischt werden.")
    status = "blocked" if any(x["level"] == "blocked" for x in issues) else "review" if issues else "plausible"
    recommendations.append({"action": "human_review", "message": "Keine Druckstartfreigabe: Druckbett, Slicerprofil, tatsächliche AMS-Belegung und Purge-/Kalibrierbedarf selbst prüfen."})
    return {"plate": plate_index, "status": status, "simulation": True, "start_authorized": False,
            "filaments": lines, "issues": issues, "recommendations": recommendations,
            "note": "3MF-Grammwerte ohne zusätzlichen Spül-/Kalibrier- oder Sicherheitszuschlag."}
