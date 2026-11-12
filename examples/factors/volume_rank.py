from factortrader import PythonIndicator


class VolumeRankFactor(PythonIndicator):
    def __init__(self, volume_period=20, rank_period=5):
        super().__init__()
        self.volume_period = volume_period
        self.rank_period = rank_period

    def update(self, input):
        volume_mean = self.ts.mean(input.volume, self.volume_period)
        first_rank = self.cs.rank_unit(volume_mean)
        rank_mean = self.ts.mean(first_rank, self.rank_period)
        self.value = self.cs.rank_unit(rank_mean)
        return self.ts.is_ready(rank_mean)
