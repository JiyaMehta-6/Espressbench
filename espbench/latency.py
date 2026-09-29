import math


def _percentile(sorted_samples, p):
    if len(sorted_samples) == 1:
        return sorted_samples[0]
    k = (len(sorted_samples) - 1) * p / 100.0
    low = int(math.floor(k))
    high = min(low + 1, len(sorted_samples) - 1)
    return sorted_samples[low] + (sorted_samples[high] - sorted_samples[low]) * (k - low)


def run(device, n=100, warmup=10):
    samples = []
    errors = 0
    for i in range(n + warmup):
        try:
            value = device.ping()
        except Exception:
            errors += 1
            continue
        if i >= warmup:
            samples.append(float(value))
    if not samples:
        return {"n": 0, "errors": errors, "failed": True}
    ordered = sorted(samples)
    deltas = [abs(samples[i] - samples[i - 1]) for i in range(1, len(samples))]
    jitter = sum(deltas) / len(deltas) if deltas else 0.0
    return {
        "n": len(samples),
        "errors": errors,
        "mean": round(sum(samples) / len(samples), 3),
        "p50": round(_percentile(ordered, 50), 3),
        "p95": round(_percentile(ordered, 95), 3),
        "p99": round(_percentile(ordered, 99), 3),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
        "jitter": round(jitter, 3),
        "_samples": [round(value, 3) for value in samples],
        "unit": "ms",
    }
