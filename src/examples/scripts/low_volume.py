from factorpool import PythonIndicator


config = {
    'type': 'REGULAR',
    'base': {
        'start_date': '2016-01-01',
        'end_date': '2026-01-01',
        'frequency': '1d',
    },
    'data': {
        'provider': 'market_matrix',
        'universe': ['000001.XSHE', '000002.XSHE', '600000.XSHG'],
        'factor_price': 'quote_adjusted',
    },
    'factor': {
        'period': 20,
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


class LowVolumeFactor(PythonIndicator):
    def __init__(self, period=20):
        super().__init__()
        self.period = period

    def update(self, input):
        mean_volume = self.ts.mean(input.volume, self.period)
        self.value = self.cs.rank_unit(-mean_volume)
        return self.ts.is_ready(mean_volume)


if __name__ == "__main__":
    LowVolumeFactor.run(config)
