from factortrader import PythonIndicator


class LowVolumeFactor(PythonIndicator):
    def __init__(self, period=20):
        super().__init__()
        self.period = period

    def update(self, input):
        mean_volume = self.ts.mean(input.volume, self.period)
        self.value = self.cs.rank_unit(-mean_volume)
        return self.ts.is_ready(mean_volume)
