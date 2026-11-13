from collections import deque
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
        'universe': 'all_a_dynamic',
        'factor_price': 'quote_adjusted',
    },
    'factor': {
        'period': 3,
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


if __name__ == "__main__":
    CloseMeanPython.run(config)
