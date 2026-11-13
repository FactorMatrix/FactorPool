#include "brain_temporal.hpp"
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
using namespace factorpool::operators;
namespace {
const double missing_value = std::numeric_limits<double>::quiet_NaN();
BrainTemporalSpec spec(const std::string& op, std::size_t d=3) { BrainTemporalSpec out; out.op=op; out.period=d; return out; }
void equal(double x, double expected, double tolerance=1e-11) { if (!std::isfinite(x) || std::abs(x-expected) > tolerance * std::max(1.,std::abs(expected))) { std::cerr << "Expected " << expected << ", got " << x << '\n'; std::abort(); } }
CalendarResult stream(BrainTemporalSpec s, const std::vector<double>& x, const std::vector<double>& y={}) {
    BrainTemporal state(s); state.set_axis({"A"}); CalendarResult result;
    for (std::size_t t=0;t<x.size();++t) {
        const auto args=s.op=="ts_step" ? std::vector<std::vector<double>>{} : y.empty() ? std::vector<std::vector<double>>{{x[t]}} : std::vector<std::vector<double>>{{x[t]},{y[t]}};
        result=state.update(t+1,args,std::vector<std::vector<std::uint8_t>>(args.size(),{1}),{1});
    }
    return result;
}
}
int main() {
    std::size_t cases=0;
    auto check=[&](BrainTemporalSpec s,const std::vector<double>& x,double expected,const std::vector<double>& y=std::vector<double>{}){const auto result=stream(s,x,y);assert(result.ready[0] && result.valid[0]);equal(result.values[0],expected);++cases;};
    check(spec("ts_mean"),{1,2,6},3);
    check(spec("ts_sum"),{1,2,6},9);
    check(spec("ts_std_dev"),{1,2,6},std::sqrt(14./3));
    auto sample=spec("ts_std_dev");sample.ddof=1;check(sample,{1,2,6},std::sqrt(7.));
    check(spec("ts_delay",2),{10,20,30},10);
    check(spec("ts_delta",2),{10,20,30},20);
    check(spec("ts_delay",0),{10},10);
    check(spec("ts_delta",0),{10},0);
    check(spec("ts_av_diff",6),{missing_value,9,5,8,2,6},0);
    check(spec("ts_arg_max",6),{4,9,5,8,2,6},4);
    check(spec("ts_arg_min",6),{4,9,5,8,2,6},1);
    check(spec("ts_arg_max"),{9,1,9},0);
    check(spec("ts_arg_min"),{1,9,1},0);
    check(spec("ts_product",5),{1.01,.99,1.02,1,1.03},1.05049494);
    check(spec("ts_count_nans",5),{100,missing_value,200,missing_value,300},2);
    check(spec("ts_backfill"),{120,missing_value,missing_value},120);
    auto second=spec("ts_backfill");second.k=2;check(second,{120,130,missing_value},120);
    auto kth=spec("kth_element",5);kth.ignore="NaN 0";check(kth,{.5,missing_value,missing_value,.7,0},.7);
    check(spec("last_diff_value"),{2,3,3},2);
    check(spec("ts_rank",5),{10,12,11,15,13},.75);
    auto shifted=spec("ts_rank",5);shifted.constant=.1;check(shifted,{10,12,11,15,13},.85);
    check(spec("ts_rank"),{2,2,2},.5);
    auto uniform=spec("ts_quantile");uniform.driver="uniform";check(uniform,{1,3,2},.5);
    auto gaussian=spec("ts_quantile");check(gaussian,{1,3,2},0);
    auto cauchy=spec("ts_quantile");cauchy.driver="cauchy";check(cauchy,{1,3,2},0);
    equal(brain_distribution_quantile(.975,"gaussian"),1.959963984540054,1e-12);++cases;
    equal(brain_distribution_quantile(.25,"cauchy"),-1);++cases;
    auto scale=spec("ts_scale",6);scale.constant=1;check(scale,{4,9,5,8,2,6},1+4./7);
    { auto z = spec("ts_zscore",3); z.ddof = 1; check(z,{1,2,3},1.); }
    check(spec("ts_zscore",5),{10,12,11,13,15},(15-12.2)/std::sqrt(2.96));
    check(spec("ts_covariance",1),{2},0,{5});
    check(spec("ts_covariance",5),{2,4,6,8,10},8,{1,3,5,7,9});
    check(spec("ts_corr",7),{2,3,5,6,3,8,10},.6890818588289359,{100,190,150,180,210,220,240});
    check(spec("ts_corr",4),{1,missing_value,3,4},1,{2,9,6,8});
    check(spec("ts_decay_linear"),{6,8,10},52./6);
    check(spec("ts_decay_linear"),{6,missing_value,10},36./6);
    auto dense=spec("ts_decay_linear");dense.dense=true;check(dense,{6,missing_value,10},36./4);
    check(spec("ts_decay_linear"),{missing_value,missing_value,missing_value},0);
    check(spec("days_from_last_change",1),{10,10,12,12,12},2);
    check(spec("days_from_last_change",1),{10,missing_value,10},0);
    check(spec("ts_step",1),{1,1,1},3);
    check(spec("ts_step",2),{1,1,1},6);
    // Exact non-degenerate OLS oracle: x=[1,2,3,4], y=[2,4,5,8].
    // meanx=2.5, meany=4.75; Sxx=5, Sxy=9.5, beta=1.9,
    // alpha=0, SSE=.7, SST=18.75. MSE uses n-2 residual degrees of freedom.
    const double expected[]={.4,0,1.9,7.6,.7,18.75,1-.7/18.75,.35,std::sqrt(.35/5),std::sqrt(.35*(.25+6.25/5))};
    for (int rettype=0;rettype<10;++rettype) {auto regression=spec("ts_regression",4);regression.rettype=rettype;check(regression,{2,4,5,8},expected[rettype],{1,2,3,4});}
    auto lag=spec("ts_regression",3);lag.lag=1;lag.rettype=2;check(lag,{99,3,5,7},2,{1,2,3,999});
    auto pair=spec("ts_regression",4);pair.rettype=2;check(pair,{2,missing_value,6,8},2,{1,2,3,4});
    auto invalid=[&](BrainTemporalSpec s,const std::vector<double>& x,const std::vector<double>& y=std::vector<double>{}) {auto out=stream(s,x,y);assert(out.ready[0] && !out.valid[0] && std::isnan(out.values[0]));++cases;};
    invalid(spec("ts_zscore"),{1,1,1});invalid(spec("ts_scale"),{1,1,1});
    invalid(spec("ts_corr"),{1,1,1},{2,3,4});invalid(spec("ts_regression"),{1,2,3},{1,1,1});
    invalid(spec("ts_backfill"),{missing_value,missing_value,missing_value});invalid(spec("last_diff_value"),{2,2,2});
    invalid(dense,{missing_value,missing_value,missing_value});invalid(spec("ts_count_nans"),{1,std::numeric_limits<double>::infinity(),3});
    invalid(spec("ts_product"),{1,missing_value,2});
    {
        BrainTemporal state(spec("ts_mean"));state.set_axis({"A"});
        assert(!state.update(1,{{1}},{{1}},{1}).ready[0]);
        assert(!state.update(2,{{2}},{{0}},{1}).ready[0]);
        assert(!state.update(3,{{3}},{{1}},{1}).ready[0]);
        assert(!state.update(4,{{4}},{{1}},{1}).ready[0]);
        auto fifth=state.update(5,{{5}},{{1}},{1});assert(fifth.ready[0] && fifth.valid[0]);equal(fifth.values[0],4);++cases;
        bool duplicate=false;try{state.update(5,{{6}},{{1}},{1});}catch(const std::invalid_argument&){duplicate=true;}assert(duplicate);
    }
    {
        BrainTemporal state(spec("ts_delay",1));state.set_axis({"A"});
        state.update(1,{{10}},{{1}},{1});
        auto absent=state.update(2,{{123}},{{1}},{0});assert(absent.ready[0] && !absent.valid[0]);
        auto after=state.update(3,{{30}},{{1}},{1});assert(after.ready[0] && !after.valid[0]);++cases;
    }
    {
        auto observations=spec("ts_delay",1);observations.valid_observations=true;
        BrainTemporal state(observations);state.set_axis({"A"});state.update(1,{{10}},{{1}},{1});
        auto missing=state.update(2,{{missing_value}},{{1}},{1});assert(!missing.valid[0]);
        auto after=state.update(3,{{30}},{{1}},{1});assert(after.valid[0]);equal(after.values[0],10);++cases;
    }
    {
        BrainTemporal state(spec("ts_mean",2));state.set_axis({"A","B"});
        state.update(1,{{1,10}},{{1,1}},{1,1});state.update(2,{{2,20}},{{1,1}},{1,1});
        state.set_axis({"B"});auto keep=state.update(3,{{30}},{{1}},{1});equal(keep.values[0],25);
        state.set_axis({"A","B"});auto fresh=state.update(4,{{4,40}},{{1,1}},{1,1});assert(!fresh.ready[0]);equal(fresh.values[1],35);++cases;
    }
    {
        auto config=spec("hump",1);config.hump=.1;BrainTemporal state(config);state.set_axis({"A","B"});
        state.update(1,{{5,10}},{{1,1}},{1,1});auto next=state.update(2,{{2,10}},{{1,1}},{1,1});equal(next.values[0],3.8);equal(next.values[1],10);++cases;
        auto tiny=state.update(3,{{4,10}},{{1,1}},{1,1});equal(tiny.values[0],3.8);
        auto unknown=state.update(4,{{missing_value,10}},{{1,1}},{1,1});assert(!unknown.valid[0]);
        auto recover=state.update(5,{{8,10}},{{1,1}},{1,1});equal(recover.values[0],5.6);++cases;
    }
    {
        BrainTemporal state(spec("ts_mean",2));state.set_axis({"A"});
        bool rejected=false;try{state.update(1,{{1,2}},{{1,1}},{1});}catch(const std::invalid_argument&){rejected=true;}assert(rejected);
        auto first=state.update(1,{{0}},{{1}},{1});assert(!first.ready[0]);
        auto second=state.update(2,{{2}},{{1}},{1});equal(second.values[0],1);
        state.update(3,{{1000}},{{1}},{1});equal(second.values[0],1);++cases;
    }
    {
        auto s=spec("ts_regression",3);s.lag=1;s.rettype=2;BrainTemporal state(s);state.set_axis({"A"});
        for(int t=1;t<=3;++t)assert(!state.update(t,{{double(2*t-1)},{double(t)}},{{1},{1}},{1}).ready[0]);
        auto last=state.update(4,{{7},{4}},{{1},{1}},{1});assert(last.ready[0]);equal(last.values[0],2);++cases;
    }
    {
        auto s=spec("ts_step",1);BrainTemporal state(s);state.set_axis({"A"});state.update(1,{}, {}, {1});
        state.set_axis({"A","B"});auto next=state.update(2,{}, {}, {1,1});equal(next.values[0],2);equal(next.values[1],2);++cases;
    }
    std::cout << "Brain temporal independent cases: " << cases << " passed\n";
}
