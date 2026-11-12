from factorpool import PythonIndicator


config = {
    'type': 'REGULAR',
    'base': {
        'start_date': '2024-01-29',
        'end_date': '2024-01-29',
        'frequency': '1d',
    },
    'data': {
        'provider': 'market_matrix',
        'universe': ['000001.XSHE', '000002.XSHE', '600000.XSHG'],
    },
    'factor': {
        'volume_period': 20,
        'rank_period': 5,
    },
    'settings': {
        'instrumentType': 'EQUITY',
        'region': 'CHN',
        'language': 'PYTHONINDICATOR',
        'delay': 1,
        'decay': 0,
        'neutralization': 'MARKET',
        'truncation': 0,
        'pasteurization': 'ON',
        'nanHandling': 'OFF',
        'unitHandling': 'OFF',
        'testPeriod': 'P0Y0M',
        'maxTrade': 'OFF',
        'maxPosition': 'OFF',
        'simulationMode': 'FULL',
        'visualization': True,
    },
    'analysis': {
        'groups': 3,
        'horizons': [1, 5, 20],
        'min_ic_pairs': 3,
        'icir_ddof': 1,
        'annualize_icir': False,
        'group_weight': 'equal',
        'decay_steps': 10,
        'annual_days': 252,
        'book_size': 1,
        'pnl_unit': 'BOOK',
        'trace_dates': [],
    },
}


class VolumeRankFactor(PythonIndicator):
    def __init__(self, volume_period=20, rank_period=5):
        super().__init__()
        self.volume_period = volume_period
        self.rank_period = rank_period

    def update(self, input):
        volume_mean = self.ts.mean(input.volume, self.volume_period)
        first_rank = self.cs.rank(volume_mean, pct=True)
        rank_mean = self.ts.mean(first_rank, self.rank_period)
        self.value = self.cs.rank(rank_mean, pct=True)
        return self.ts.is_ready(rank_mean)


if __name__ == "__main__":
    VolumeRankFactor.run(config)
