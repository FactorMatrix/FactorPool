from collections import deque
from factortrader import PythonIndicator


class CloseMeanPython(PythonIndicator):
    def __init__(self, period=3):
        super().__init__()
        self.period = period
        self.warm_up_period = period
        self.values = deque(maxlen=period)

    def update(self, input):
        self.values.append(input.close)
        self.value = sum(self.values) / len(self.values)
        return len(self.values) == self.period

    def reset(self):
        self.values.clear()
        super().reset()
