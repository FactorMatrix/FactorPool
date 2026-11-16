// Sectioned HTTP fixtures mirror the frontend's public report endpoints.
const pick = (value, keys) => Object.fromEntries(keys.filter(key => value?.[key] !== undefined).map(key => [key, value[key]]))
const alphaKeys = ['brain_style','yearly_stats','pnl','nav','drawdown_series','returns','turnover_series','monthly','yearly','sharpe_series']
export function reportSection(report, section='core') {
  if (section === 'export') return report
  const metadata = report.metadata || {}, diagnostics = report.diagnostics || {}, h = diagnostics.horizons?.['1']
  if (section === 'source') return {metadata}
  let horizon
  const conventions = pick(diagnostics.conventions, section === 'core' ? ['brain_settings','missing_datasets'] : ['annual_days','brain_settings'])
  if (h && section === 'core') {
    horizon = {...pick(h,['horizon','portfolio_dates']), portfolios:{alpha:pick(h.portfolios?.alpha,alphaKeys)}}
    const partitions = {}
    if (h.partitions?.train) partitions.train = {dates:h.partitions.train.dates,alpha:pick(h.partitions.train.alpha,alphaKeys)}
    if (h.partitions?.test) partitions.test = {dates:h.partitions.test.dates,alpha:pick(h.partitions.test.alpha,['brain_style'])}
    horizon.partitions = partitions
  } else if (h && section === 'ic') horizon = pick(h,['horizon','dates','ic','rank_ic','series','monthly','yearly','decay'])
  else if (h && section === 'groups') horizon = pick(h,['horizon','portfolio_dates','portfolios'])
  else if (h && section === 'trace') horizon = pick(h,['horizon','pnl_book','alpha_trace','trace_status','alpha_constraints'])
  const projected = {metadata:pick(metadata, section === 'core'
    ? ['report_version','brain_settings','source_sha256','snapshot_id','synthetic','value_count'] : ['report_version']),
    diagnostics:{version:diagnostics.version,conventions,horizons:horizon ? {'1':horizon} : {}}}
  if (section === 'core') {
    projected.diagnostics.test_period = diagnostics.test_period
    if (diagnostics.alpha_constraints) projected.diagnostics.alpha_constraints = {
      ...pick(diagnostics.alpha_constraints,['policy','truncation','feasibility_scope']),
      daily:diagnostics.alpha_constraints.daily?.map(row=>pick(row,['date','full_gross_feasible','target_gross','unallocated_gross'])),
    }
  } else if (section === 'ic') projected.summary = report.summary
  else if (section === 'trace') projected.diagnostics.alpha_constraints = diagnostics.alpha_constraints
  return projected
}
