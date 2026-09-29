import csv


def _rows(text):
    rows = []
    for row in csv.reader(text.splitlines()):
        if len(row) < 2:
            continue
        try:
            rows.append((float(row[0]), float(row[1])))
        except ValueError:
            continue
    if not rows:
        raise ValueError("no numeric (time_ms, mA) rows found")
    rows.sort()
    return rows


def parse(text):
    return _rows(text)


def parse_markers(text):
    markers = []
    for row in csv.reader(text.splitlines()):
        if len(row) < 2:
            continue
        try:
            markers.append((float(row[0]), row[1].strip()))
        except ValueError:
            continue
    if not markers:
        raise ValueError("no numeric (time_ms, label) marker rows found")
    return sorted(markers)


def summarize(rows):
    readings = [ma for _, ma in rows]
    duration = (rows[-1][0] - rows[0][0]) / 1000.0
    return {
        "samples": len(rows),
        "duration_s": round(duration, 3),
        "mean_ma": round(sum(readings) / len(readings), 3),
        "peak_ma": round(max(readings), 3),
        "min_ma": round(min(readings), 3),
    }


def energy_mah(rows):
    total = 0.0
    for i in range(1, len(rows)):
        dt_ms = rows[i][0] - rows[i - 1][0]
        avg_ma = (rows[i][1] + rows[i - 1][1]) / 2.0
        total += avg_ma * dt_ms
    return total / 3_600_000.0


def attribute(rows, markers):
    if len(rows) < 2 or not markers:
        return []
    out = []
    for i, (start, label) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else rows[-1][0]
        window = [row for row in rows if start <= row[0] <= end]
        if len(window) < 2:
            out.append({"label": label, "duration_s": 0.0, "mean_ma": 0.0,
                        "energy_mah": 0.0})
            continue
        stats = summarize(window)
        out.append({"label": label,
                    "duration_s": stats["duration_s"],
                    "mean_ma": stats["mean_ma"],
                    "energy_mah": round(energy_mah(window), 6)})
    return out


def battery_life_hours(rows, capacity_mah):
    if capacity_mah <= 0:
        raise ValueError("capacity_mah must be positive")
    stats = summarize(rows)
    if stats["mean_ma"] <= 0:
        return None
    return round(capacity_mah / stats["mean_ma"], 2)
