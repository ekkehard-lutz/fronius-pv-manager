"""HA publication precision and bounded informative efficiency sample windows."""

from collections import deque
from decimal import ROUND_HALF_UP, Decimal, localcontext
from statistics import median

RESOLUTIONS = {"V": Decimal("0.1"), "A": Decimal("0.001"), "W": Decimal("0.1")}


def normalize_measurement(value, unit):
    """Round decimal engineering values only at the sensor publication boundary."""
    quantum = RESOLUTIONS.get(unit)
    if value is None or quantum is None or isinstance(value, bool):
        return value
    number = Decimal(str(value))
    if not number.is_finite():
        return None
    with localcontext() as context:
        context.prec = max(28, number.adjusted() + 8, len(number.as_tuple().digits) + 8)
        rounded = number.quantize(quantum, rounding=ROUND_HALF_UP)
    return float(rounded) if rounded else 0.0


class EfficiencyWindow:
    """Keep actual valid samples in [now - 60, now], with no synthetic refill."""

    def __init__(self):
        self.samples = deque()
        self.mode = None
        self.pending = False

    def observe(self, now, value, mode):
        if mode is not None and mode != self.mode:
            self.samples.clear()
            self.pending = False
            self.mode = mode
        while self.samples and self.samples[0][0] < now - 60:
            self.samples.popleft()
        if value is not None:
            self.samples.append((now, value))
            self.pending = True

    def publish(self):
        if not self.samples or not self.pending:
            return None
        self.pending = False
        values = [Decimal(str(v)) for _, v in self.samples]
        with localcontext() as context:
            context.prec = max(
                28,
                max(v.adjusted() for v in values) + 8,
                max(len(v.as_tuple().digits) for v in values) + 8,
            )
            value = median(values)
            return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
