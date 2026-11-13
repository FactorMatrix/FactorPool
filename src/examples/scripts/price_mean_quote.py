from factorpool import PythonIndicator


config = {
    'type': 'REGULAR',
    'base': {'start_date': '2024-01-02', 'end_date': '2024-01-12', 'frequency': '1d'},
    'data': {
        'provider': 'market_matrix',
        'universe': ['000001.XSHE', '000002.XSHE', '600000.XSHG'],
        'factor_price': 'quote_adjusted',
    },
    'factor': {'period': 2},
    'settings': {'delay': 1, 'decay': 0, 'neutralization': 'MARKET', 'truncation': 0},
    'analysis': {'groups': 3, 'horizons': [1, 5, 20]},
}


class QuotePriceMean(PythonIndicator):
    def __init__(self, period=2):
        super().__init__()
        self.period = period

    def update(self, input):
        mean_close = self.ts.mean(input.close, self.period)
        self.value = mean_close
        return self.ts.is_ready(mean_close)


if __name__ == '__main__':
    QuotePriceMean.run(config)
