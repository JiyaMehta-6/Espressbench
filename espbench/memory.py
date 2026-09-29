import time


def _slope(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom


def run(device, samples=25, interval=1.0, drop_threshold=2048):
    series = []
    errors = 0
    for _ in range(samples):
        try:
            stats = device.stats()
            uptime = stats["uptime_s"]
            free = stats["mem_free"]
            if free is None:
                raise ValueError("mem_free unavailable")
        except Exception:
            errors += 1
            continue
        series.append((uptime, free))
        time.sleep(interval)
    if len(series) < 2:
        return {"samples": len(series), "errors": errors, "failed": True}
    uptimes = [row[0] for row in series]
    memory = [row[1] for row in series]
    restarted = any(uptimes[i] < uptimes[i - 1] for i in range(1, len(uptimes)))
    drop = memory[0] - memory[-1]
    slope = _slope([float(u) for u in uptimes], [float(m) for m in memory])
    leak = bool(drop >= drop_threshold and slope < 0)
    return {
        "samples": len(series),
        "errors": errors,
        "start_free": memory[0],
        "end_free": memory[-1],
        "drop_bytes": drop,
        "bytes_per_s": round(slope, 3),
        "leak_detected": leak,
        "device_restarted": restarted,
    }
